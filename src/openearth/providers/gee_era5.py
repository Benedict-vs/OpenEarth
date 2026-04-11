"""Earth Engine provider for ERA5-Land hourly wind data."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import ee

from openearth.analytics.conversions import to_ee_date

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
    n_points: int = 25,
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

    points = []
    for i in range(1, side + 1):
        for j in range(1, side + 1):
            lon = west + i * lon_step
            lat = south + j * lat_step
            points.append(ee.Geometry.Point(lon, lat))

    fc = ee.FeatureCollection(
        [ee.Feature(p) for p in points],
    )

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
        })
    return results
