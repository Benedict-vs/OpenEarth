"""Earth Engine provider for ERA5-Land hourly wind data."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import ee

ERA5_COLLECTION_ID = "ECMWF/ERA5_LAND/HOURLY"


def get_wind_data(
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    half_window_hours: int = 12,
) -> ee.Image:
    """Return a 2-band (u10, v10) mean wind image for *target_date*.

    Uses ERA5-Land hourly u/v wind components at 10 m height.
    Composites over +/- *half_window_hours* around noon UTC.
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    center = datetime(
        target_date.year, target_date.month,
        target_date.day, 12, 0,
    )
    start = center - timedelta(hours=half_window_hours)
    end = center + timedelta(hours=half_window_hours)

    col = (
        ee.ImageCollection(ERA5_COLLECTION_ID)
        .filterDate(
            ee.Date(start.isoformat()),
            ee.Date(end.isoformat()),
        )
        .filterBounds(geometry)
        .select([
            "u_component_of_wind_10m",
            "v_component_of_wind_10m",
        ])
    )
    return col.mean().clip(geometry)


def sample_wind_grid(
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    n_points: int = 100,
    half_window_hours: int = 12,
) -> list[dict[str, Any]]:
    """Sample wind at a grid of points within the ROI.

    Returns a list of dicts with keys:
    ``lon``, ``lat``, ``u``, ``v``, ``speed``,
    ``direction_deg``.
    """
    wind_image = get_wind_data(
        geometry, target_date, half_window_hours,
    )

    # Compute wind speed and direction server-side.
    u = wind_image.select("u_component_of_wind_10m")
    v = wind_image.select("v_component_of_wind_10m")
    speed = (
        u.pow(2).add(v.pow(2)).sqrt()
        .rename("wind_speed")
    )
    # atan2(u, v) gives meteorological direction.
    direction = (
        u.atan2(v)
        .multiply(180 / math.pi)
        .rename("wind_dir")
    )

    combined = wind_image.addBands(speed).addBands(
        direction,
    )

    # Generate grid points from the bounding box.
    coords = geometry.bounds().coordinates().getInfo()
    ring = coords[0]
    lons = [float(p[0]) for p in ring]
    lats = [float(p[1]) for p in ring]
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)

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
                ee.Feature(
                    ee.Geometry.Point(lon, lat),
                    {"density_level": level},
                ),
            )

    fc = ee.FeatureCollection(points)

    sampled = combined.sampleRegions(
        collection=fc,
        scale=11132,  # ~0.1 degree at equator
        geometries=True,
    ).getInfo()

    results = []
    for feat in sampled.get("features", []):
        props = feat["properties"]
        crds = feat["geometry"]["coordinates"]
        results.append({
            "lon": crds[0],
            "lat": crds[1],
            "u": props.get("u_component_of_wind_10m"),
            "v": props.get("v_component_of_wind_10m"),
            "speed": props.get("wind_speed"),
            "direction_deg": props.get("wind_dir"),
            "density_level": props.get("density_level", 0),
        })
    return results
