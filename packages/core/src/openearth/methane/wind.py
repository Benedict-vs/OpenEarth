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

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import ee
import numpy as np

from openearth.ee.client import ee_call
from openearth.errors import EmptyCollectionError

if TYPE_CHECKING:
    from openearth.geometry import ROI, BBox

ERA5_LAND_HOURLY_ID = "ECMWF/ERA5_LAND/HOURLY"
# Global ERA5 hourly (id verified against the EE catalog). Unlike ERA5-Land it
# has values over open water, so it is the fallback when the land product is
# fully masked over an ROI.
GLOBAL_ERA5_HOURLY_ID = "ECMWF/ERA5/HOURLY"
_U_BAND = "u_component_of_wind_10m"
_V_BAND = "v_component_of_wind_10m"
# ERA5-Land native resolution is ~0.1° (~11 km at the equator).
_ERA5_SCALE_M = 11_132
# A field this coarse is browsing context, not analysis; cap the reduceRegions
# feature count so a stray request can't ask for millions of cells.
_MAX_FIELD_DIM = 50


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


def _to_utc(when: datetime) -> datetime:
    """Normalize *when* to timezone-aware UTC (naive input is assumed UTC)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _bracketing_images(when: datetime, collection_id: str) -> tuple[ee.Image, ee.Image, float]:
    """The two ERA5 hourly grids bracketing *when*, plus the interpolation weight.

    *weight* is the fraction of the hour elapsed at *when* — 0 selects
    ``img_before``, 1 selects ``img_after``. Both images carry only the u/v
    10 m bands. Assumes *when* is already UTC (see :func:`_to_utc`).
    """
    before = _hour_floor(when)
    after = before + timedelta(hours=1)
    weight = (when - before).total_seconds() / 3600.0

    collection = (
        ee.ImageCollection(collection_id)
        .filterDate(
            ee.Date(before.isoformat()),
            ee.Date((after + timedelta(hours=1)).isoformat()),
        )
        .select([_U_BAND, _V_BAND])
    )
    img_before = ee.Image(
        collection.filter(ee.Filter.eq("system:time_start", int(before.timestamp() * 1000))).first()
    )
    img_after = ee.Image(
        collection.filter(ee.Filter.eq("system:time_start", int(after.timestamp() * 1000))).first()
    )
    return img_before, img_after, weight


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
    when = _to_utc(when)
    img_before, img_after, weight = _bracketing_images(when, collection_id)
    geometry = roi.to_ee_geometry()

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

    stats_before = _roi_mean(img_before)
    stats_after = _roi_mean(img_after)

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


# ── Wind field over a bbox (browsing context) ────────────────────


@dataclass(frozen=True)
class WindCell:
    """One cell of an ``nx × ny`` lattice over a bbox, indexed row-major from NW."""

    idx: int
    west: float
    south: float
    east: float
    north: float

    @property
    def center(self) -> tuple[float, float]:
        """(lon, lat) of the cell center."""
        return ((self.west + self.east) / 2.0, (self.south + self.north) / 2.0)


def wind_grid(bbox: BBox, nx: int, ny: int) -> list[WindCell]:
    """Partition *bbox* into ``nx × ny`` equal cells, row-major from the NW corner.

    Row 0 is the northernmost row (indices increase west→east); rows then advance
    southward, so ``idx == row * nx + col``. Pure geometry — no Earth Engine — so
    the field's layout is unit-tested offline.
    """
    if not (1 <= nx <= _MAX_FIELD_DIM and 1 <= ny <= _MAX_FIELD_DIM):
        raise ValueError(f"nx and ny must be in [1, {_MAX_FIELD_DIM}]; got {nx}x{ny}.")
    dx = bbox.width_deg / nx
    dy = bbox.height_deg / ny
    cells: list[WindCell] = []
    for row in range(ny):
        north = bbox.north - row * dy
        south = north - dy
        for col in range(nx):
            west = bbox.west + col * dx
            cells.append(
                WindCell(idx=row * nx + col, west=west, south=south, east=west + dx, north=north)
            )
    return cells


@dataclass(frozen=True)
class WindField:
    """ROI wind field: per-cell mean 10 m u/v on an ``nx × ny`` lattice.

    ``u``/``v`` are row-major from the NW corner (aligned with :func:`wind_grid`);
    a fully-masked cell is ``NaN`` rather than absent, so both arrays always have
    length ``nx * ny``.
    """

    when: datetime
    bbox: BBox
    nx: int
    ny: int
    u: tuple[float, ...]
    v: tuple[float, ...]
    collection_id: str


def _field_arrays_from_features(
    features: list[dict[str, Any]], n: int
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Row-major (u, v) arrays of length *n* from ``reduceRegions`` output features.

    Features carry the cell's ``idx`` and may arrive in any order; a masked cell
    (feature absent, or its band property ``None``) becomes ``NaN``. Pure — no
    Earth Engine — so the stitching and masking are unit-tested offline.
    """
    by_idx: dict[int, tuple[float, float]] = {}
    for feature in features:
        props = feature.get("properties", {})
        u = props.get(_U_BAND)
        v = props.get(_V_BAND)
        by_idx[int(props["idx"])] = (
            float(u) if u is not None else math.nan,
            float(v) if v is not None else math.nan,
        )
    missing = (math.nan, math.nan)
    return (
        tuple(by_idx.get(i, missing)[0] for i in range(n)),
        tuple(by_idx.get(i, missing)[1] for i in range(n)),
    )


def sample_wind_field(
    bbox: BBox,
    when: datetime,
    nx: int,
    ny: int,
    *,
    collection_id: str = ERA5_LAND_HOURLY_ID,
    fallback_collection_id: str | None = None,
) -> WindField:
    """Per-cell mean 10 m wind over *bbox* at *when*, in a single EE round-trip.

    Blends the two bracketing ERA5 grids in time *server-side* (so a mask in
    either grid masks the result), then reduces the blended image over an
    ``nx × ny`` FeatureCollection of cell rectangles with one
    ``reduceRegions``/``getInfo``. Fully-masked cells become ``NaN``; if the whole
    field is masked (e.g. ERA5-Land over open water) and *fallback_collection_id*
    is given, it retries once against that collection.
    """
    when = _to_utc(when)
    img_before, img_after, weight = _bracketing_images(when, collection_id)
    interp = img_before.multiply(1.0 - weight).add(img_after.multiply(weight))

    cells = wind_grid(bbox, nx, ny)
    features = ee.FeatureCollection(
        [
            ee.Feature(ee.Geometry.Rectangle([c.west, c.south, c.east, c.north]), {"idx": c.idx})
            for c in cells
        ]
    )
    reduced = interp.reduceRegions(
        collection=features,
        reducer=ee.Reducer.mean(),
        scale=_ERA5_SCALE_M,
    )
    info = ee_call(reduced.getInfo) or {}
    u_vals, v_vals = _field_arrays_from_features(info.get("features", []), nx * ny)

    if fallback_collection_id is not None and all(math.isnan(x) for x in u_vals):
        return sample_wind_field(
            bbox,
            when,
            nx,
            ny,
            collection_id=fallback_collection_id,
            fallback_collection_id=None,
        )

    return WindField(
        when=when,
        bbox=bbox,
        nx=nx,
        ny=ny,
        u=u_vals,
        v=v_vals,
        collection_id=collection_id,
    )
