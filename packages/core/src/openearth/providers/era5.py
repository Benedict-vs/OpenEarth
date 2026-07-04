"""Earth Engine provider for ERA5-Land hourly wind fields (map overlays).

For point/ROI samples matched to a satellite overpass use
:mod:`openearth.methane.wind`; this module builds the gridded wind field for
map arrow/particle overlays. v2 fixes vs the v1 provider:

- the composite window is centred on a caller-supplied instant (the actual
  scene time), not hardcoded noon UTC;
- direction is returned under BOTH conventions, explicitly named
  (``wind_to_deg`` / ``wind_from_deg``) — v1 mislabeled the blowing-toward
  azimuth as meteorological.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import ee

from openearth.ee.client import ee_call
from openearth.geometry import BBox
from openearth.methane.wind import ERA5_LAND_HOURLY_ID

if TYPE_CHECKING:
    from openearth.geometry import ROI

_U_BAND = "u_component_of_wind_10m"
_V_BAND = "v_component_of_wind_10m"


def _coerce_when(when: str | date | datetime) -> datetime:
    if isinstance(when, datetime):
        dt = when
    elif isinstance(when, date):
        dt = datetime(when.year, when.month, when.day, 12, 0)  # date-only → midday
    else:
        dt = datetime.fromisoformat(when)
        if dt.hour == 0 and dt.minute == 0 and "T" not in when:
            dt = dt.replace(hour=12)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def get_wind_image(
    roi: ROI,
    when: str | date | datetime,
    half_window_hours: int = 3,
) -> ee.Image:
    """Return a 2-band (u10, v10) mean wind image around *when*.

    Composites ERA5-Land hourly wind over ± *half_window_hours* around the
    given instant (a bare date means midday UTC).
    """
    centre = _coerce_when(when)
    start = centre - timedelta(hours=half_window_hours)
    end = centre + timedelta(hours=half_window_hours)

    geometry = roi.to_ee_geometry()
    col = (
        ee.ImageCollection(ERA5_LAND_HOURLY_ID)
        .filterDate(ee.Date(start.isoformat()), ee.Date(end.isoformat()))
        .filterBounds(geometry)
        .select([_U_BAND, _V_BAND])
    )
    return col.mean().clip(geometry)


def sample_wind_grid(
    roi: ROI,
    when: str | date | datetime,
    n_points: int = 100,
    half_window_hours: int = 3,
) -> list[dict[str, Any]]:
    """Sample wind at a grid of points within the ROI.

    Returns dicts with keys ``lon``, ``lat``, ``u``, ``v``, ``speed``,
    ``wind_to_deg``, ``wind_from_deg``, ``density_level``. Density levels
    (0 = coarsest uniform subgrid) let the UI thin arrows by zoom.
    """
    wind_image = get_wind_image(roi, when, half_window_hours)

    # Compute wind speed and both direction conventions server-side.
    u = wind_image.select(_U_BAND)
    v = wind_image.select(_V_BAND)
    speed = u.pow(2).add(v.pow(2)).sqrt().rename("wind_speed")
    # atan2(u, v) = azimuth the wind blows TOWARD; normalize to [0, 360).
    to_deg = u.atan2(v).multiply(180 / math.pi).add(360).mod(360).rename("wind_to_deg")
    from_deg = to_deg.add(180).mod(360).rename("wind_from_deg")

    combined = wind_image.addBands(speed).addBands(to_deg).addBands(from_deg)

    bbox = roi if isinstance(roi, BBox) else roi.bounds
    west, south, east, north = bbox.as_tuple()

    side = max(2, int(math.sqrt(n_points)))
    lon_step = (east - west) / (side + 1)
    lat_step = (north - south) / (side + 1)

    # Pre-compute uniform subgrid indices for each density level.
    n0 = max(2, side // 3)  # ~3 points per axis for level 0
    n1 = max(2, side // 2)  # ~5 points per axis for level 1
    idx0 = {round(k * (side - 1) / (n0 - 1)) for k in range(n0)}
    idx1 = {round(k * (side - 1) / (n1 - 1)) for k in range(n1)}

    points = []
    for i in range(side):
        for j in range(side):
            lon = west + (i + 1) * lon_step
            lat = south + (j + 1) * lat_step
            if i in idx0 and j in idx0:
                level = 0
            elif i in idx1 and j in idx1:
                level = 1
            else:
                level = 2
            points.append(
                ee.Feature(ee.Geometry.Point(lon, lat), {"density_level": level}),
            )

    fc = ee.FeatureCollection(points)

    sampled = (
        ee_call(
            combined.sampleRegions(
                collection=fc,
                scale=11132,  # ~0.1 degree at equator
                geometries=True,
            ).getInfo,
        )
        or {}
    )

    results = []
    for feat in sampled.get("features", []):
        props = feat["properties"]
        crds = feat["geometry"]["coordinates"]
        results.append(
            {
                "lon": crds[0],
                "lat": crds[1],
                "u": props.get(_U_BAND),
                "v": props.get(_V_BAND),
                "speed": props.get("wind_speed"),
                "wind_to_deg": props.get("wind_to_deg"),
                "wind_from_deg": props.get("wind_from_deg"),
                "density_level": props.get("density_level", 0),
            }
        )
    return results
