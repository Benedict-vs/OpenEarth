"""Overpass-matched ERA5 wind sampling with explicit direction conventions.

Fixes two v1 defects (``legacy/src/openearth/providers/gee_era5.py``):

1. **Direction convention.** v1 computed ``atan2(u, v)`` and labeled it
   "meteorological direction". ``atan2(u, v)`` is the azimuth the wind blows
   *toward*, in (−180, 180]. The meteorological convention is the direction
   the wind blows *from*. v2 returns BOTH, explicitly named
   ``wind_to_deg`` / ``wind_from_deg``, normalized to [0, 360).
2. **Overpass matching.** v1 averaged a fixed noon ± 12 h window regardless
   of when the satellite actually passed. v2 samples the two ERA5 hourly
   grids bracketing the scene's ``system:time_start`` and interpolates in
   time.

The pure math lives in module-level functions so the conventions are unit
tested without Earth Engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import ee
import numpy as np

from openearth.ee.client import ee_call
from openearth.errors import EmptyCollectionError

if TYPE_CHECKING:
    from openearth.geometry import ROI

ERA5_LAND_HOURLY_ID = "ECMWF/ERA5_LAND/HOURLY"
_U_BAND = "u_component_of_wind_10m"
_V_BAND = "v_component_of_wind_10m"
# ERA5-Land native resolution is ~0.1° (~11 km at the equator).
_ERA5_SCALE_M = 11_132


# ── Pure conventions (unit-tested offline) ───────────────────────


def wind_speed(u: float | np.ndarray, v: float | np.ndarray) -> float | np.ndarray:
    """Wind speed (m/s) from u (eastward) and v (northward) components."""
    return np.hypot(u, v)


def wind_to_deg(u: float | np.ndarray, v: float | np.ndarray) -> float | np.ndarray:
    """Azimuth the wind blows TOWARD, degrees clockwise from north, [0, 360)."""
    return np.mod(np.degrees(np.arctan2(u, v)), 360.0)


def wind_from_deg(u: float | np.ndarray, v: float | np.ndarray) -> float | np.ndarray:
    """Azimuth the wind blows FROM (meteorological convention), [0, 360)."""
    return np.mod(np.asarray(wind_to_deg(u, v)) + 180.0, 360.0)


# ── Overpass-matched sampling ────────────────────────────────────


@dataclass(frozen=True)
class WindSample:
    """ROI-mean 10 m wind at a specific instant."""

    when: datetime
    u_ms: float
    v_ms: float
    speed_ms: float
    wind_to_deg: float
    wind_from_deg: float
    collection_id: str

    @classmethod
    def from_uv(cls, when: datetime, u: float, v: float, collection_id: str) -> WindSample:
        return cls(
            when=when,
            u_ms=u,
            v_ms=v,
            speed_ms=float(wind_speed(u, v)),
            wind_to_deg=float(wind_to_deg(u, v)),
            wind_from_deg=float(wind_from_deg(u, v)),
            collection_id=collection_id,
        )


def _hour_floor(when: datetime) -> datetime:
    return when.replace(minute=0, second=0, microsecond=0)


def sample_wind_at(
    roi: ROI,
    when: datetime,
    *,
    collection_id: str = ERA5_LAND_HOURLY_ID,
    fallback_collection_id: str | None = None,
) -> WindSample:
    """ROI-mean 10 m wind at *when*, time-interpolated between hourly grids.

    Selects the two ERA5 hourly images bracketing *when* (e.g. an S2 scene's
    ``system:time_start``), reduces each to the ROI mean, and interpolates
    linearly in time. ERA5-Land has no values over open water; pass a
    *fallback_collection_id* (e.g. a global ERA5 hourly collection) to retry
    when the primary sample is fully masked.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    when = when.astimezone(UTC)

    before = _hour_floor(when)
    after = before + timedelta(hours=1)
    weight = (when - before).total_seconds() / 3600.0

    geometry = roi.to_ee_geometry()
    collection = (
        ee.ImageCollection(collection_id)
        .filterDate(
            ee.Date(before.isoformat()),
            ee.Date((after + timedelta(hours=1)).isoformat()),
        )
        .select([_U_BAND, _V_BAND])
    )

    def _roi_mean(image: ee.Image) -> dict[str, float | None]:
        stats = (
            ee_call(
                image.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=geometry,
                    scale=_ERA5_SCALE_M,
                    bestEffort=True,
                    maxPixels=int(1e8),
                ).getInfo,
            )
            or {}
        )
        return {"u": stats.get(_U_BAND), "v": stats.get(_V_BAND)}

    img_before = collection.filter(
        ee.Filter.eq("system:time_start", int(before.timestamp() * 1000))
    ).first()
    img_after = collection.filter(
        ee.Filter.eq("system:time_start", int(after.timestamp() * 1000))
    ).first()

    stats_before = _roi_mean(ee.Image(img_before))
    stats_after = _roi_mean(ee.Image(img_after))

    def _interp(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return a if b is None else b
        return a * (1.0 - weight) + b * weight

    u = _interp(stats_before["u"], stats_after["u"])
    v = _interp(stats_before["v"], stats_after["v"])

    if u is None or v is None:
        if fallback_collection_id is not None:
            return sample_wind_at(
                roi,
                when,
                collection_id=fallback_collection_id,
                fallback_collection_id=None,
            )
        raise EmptyCollectionError(
            f"No {collection_id} wind values over this ROI at {when.isoformat()} "
            "(ERA5-Land is masked over open water — supply fallback_collection_id)."
        )

    return WindSample.from_uv(when, float(u), float(v), collection_id)
