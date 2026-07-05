"""Wind endpoints: a point sample and a gridded field, both EE-backed and cached.

GET with query params (not POST): these are simple, cacheable, viewport-driven
reads the map overlay hits on every pan. A bad box raises ``InvalidROIError`` →
422 via the global handler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import diskcache
from fastapi import APIRouter, Depends, Query

from openearth.geometry import BBox
from openearth_api.deps import ensure_ee, get_cache
from openearth_api.schemas import WindFieldOut, WindSampleOut
from openearth_api.services.wind import ny_from_aspect, wind_field, wind_point

router = APIRouter(tags=["wind"], dependencies=[Depends(ensure_ee)])


_TimeQuery = Annotated[datetime, Query(description="Sample instant (ISO 8601; naive is UTC).")]


@router.get("/wind")
def wind_point_route(
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    time: _TimeQuery,
) -> WindSampleOut:
    return wind_point(lat, lon, time, cache)


@router.get("/wind/field")
def wind_field_route(
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
    west: Annotated[float, Query(ge=-180, le=180)],
    south: Annotated[float, Query(ge=-90, le=90)],
    east: Annotated[float, Query(ge=-180, le=180)],
    north: Annotated[float, Query(ge=-90, le=90)],
    time: _TimeQuery,
    nx: Annotated[int, Query(ge=1, le=50)] = 24,
    ny: Annotated[int | None, Query(ge=1, le=50, description="Rows; default from aspect.")] = None,
) -> WindFieldOut:
    bbox = BBox(west, south, east, north)  # InvalidROIError → 422
    rows = ny if ny is not None else ny_from_aspect(bbox, nx)
    return wind_field(bbox, time, nx, rows, cache)
