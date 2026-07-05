"""Wind sampling: point and gridded field, both diskcached.

``sample_wind_at`` / ``sample_wind_field`` are imported by name so offline
tests fake them (see ``packages/api/tests/test_wind.py``). ERA5-Land is the
primary source; the global ERA5 hourly collection is the open-water fallback.
NaN cells (fully masked) cross the API boundary as JSON ``null``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from openearth.geometry import BBox
from openearth.methane.wind import (
    GLOBAL_ERA5_HOURLY_ID,
    sample_wind_at,
    sample_wind_field,
)
from openearth_api.cache import cache_key, roi_key_part, ttl_for
from openearth_api.schemas import BBoxIn, WindFieldOut, WindSampleOut

if TYPE_CHECKING:
    from datetime import datetime

    import diskcache

# The point endpoint samples a small box around the click so ERA5's coarse grid
# actually contains a pixel; ±0.05° ≈ 5.5 km, well under the ~11 km ERA5 cell.
_POINT_HALF_DEG = 0.05


def _nan_to_none(values: tuple[float, ...]) -> list[float | None]:
    return [None if math.isnan(x) else x for x in values]


def ny_from_aspect(bbox: BBox, nx: int, *, max_dim: int = 50) -> int:
    """Rows keeping field cells ~square given *nx* columns over *bbox*.

    Uses the cosine-corrected aspect ratio, so a wide box gets fewer rows.
    Clamped to ``[1, max_dim]`` to match the core sampler's guard.
    """
    return max(1, min(max_dim, round(nx / bbox.aspect_ratio())))


def wind_point(lat: float, lon: float, when: datetime, cache: diskcache.Cache) -> WindSampleOut:
    key = cache_key("wind_point", lat=round(lat, 3), lon=round(lon, 3), when=when.isoformat())
    cached = cache.get(key)
    if cached is not None:
        return WindSampleOut.model_validate(cached)

    half = _POINT_HALF_DEG
    bbox = BBox(
        max(-180.0, lon - half),
        max(-90.0, lat - half),
        min(180.0, lon + half),
        min(90.0, lat + half),
    )
    sample = sample_wind_at(bbox, when, fallback_collection_id=GLOBAL_ERA5_HOURLY_ID)
    out = WindSampleOut(
        when=sample.when,
        u_ms=sample.u_ms,
        v_ms=sample.v_ms,
        speed_ms=sample.speed_ms,
        wind_to_deg=sample.wind_to_deg,
        wind_from_deg=sample.wind_from_deg,
        collection_id=sample.collection_id,
    )
    cache.set(key, out.model_dump(mode="json"), expire=ttl_for(when.date()))
    return out


def wind_field(
    bbox: BBox, when: datetime, nx: int, ny: int, cache: diskcache.Cache
) -> WindFieldOut:
    key = cache_key("wind_field", bbox=roi_key_part(bbox), when=when.isoformat(), nx=nx, ny=ny)
    cached = cache.get(key)
    if cached is not None:
        return WindFieldOut.model_validate(cached)

    field = sample_wind_field(bbox, when, nx, ny, fallback_collection_id=GLOBAL_ERA5_HOURLY_ID)
    out = WindFieldOut(
        when=field.when,
        bbox=BBoxIn(west=bbox.west, south=bbox.south, east=bbox.east, north=bbox.north),
        nx=field.nx,
        ny=field.ny,
        u=_nan_to_none(field.u),
        v=_nan_to_none(field.v),
        collection_id=field.collection_id,
    )
    cache.set(key, out.model_dump(mode="json"), expire=ttl_for(when.date()))
    return out
