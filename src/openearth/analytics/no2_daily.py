"""Build daily NO2 ROI time series from Earth Engine collections."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import ee
import pandas as pd

from openearth.analytics.conversions import to_ee_date
from openearth.providers.gee_no2 import NO2_BAND, get_no2_collection

DEFAULT_SCALE_METERS = 11_132
DEFAULT_MAX_PIXELS = 1_000_000_000


def build_no2_daily_timeseries(
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    scale_meters: int = DEFAULT_SCALE_METERS,
    reducer: ee.Reducer | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    best_effort: bool = True,
) -> pd.DataFrame:
    """Compute daily NO2 statistics for an ROI and return a pandas DataFrame.

    Date handling follows Earth Engine semantics:
    - start_date is inclusive
    - end_date is exclusive

    Output columns:
    - date (YYYY-MM-DD)
    - no2_value
    - n_images
    - valid_pixel_count
    - total_pixel_count
    - coverage_fraction
    """
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    if reducer is None:
        reducer = ee.Reducer.mean()

    collection = get_no2_collection(geometry, start_date, end_date)

    # calculate the total possible number of pixels in the ROI
    total_pixel_count = (
        ee.Image.constant(1)
        .rename("ones")
        .clip(geometry)
        .reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=geometry,
            scale=scale_meters,
            maxPixels=max_pixels,
            bestEffort=best_effort,
        )
        .get("ones")
    )

    n_days = end.difference(start, "day").toInt()
    day_offsets = ee.List(
        ee.Algorithms.If(n_days.gt(0),
                         ee.List.sequence(0, n_days.subtract(1)),
                         ee.List([]))
    )

    def build_day_feature(day_offset: ee.Number) -> ee.Feature:
        day_offset = ee.Number(day_offset)
        day_start = start.advance(day_offset, "day")
        day_end = day_start.advance(1, "day")
        daily_collection = collection.filterDate(day_start, day_end)
        n_images = daily_collection.size()

        def with_data() -> ee.Dictionary:
            daily_image = ee.Image(daily_collection.mean()).select(NO2_BAND)
            no2_value = daily_image.reduceRegion(
                reducer=reducer,
                geometry=geometry,
                scale=scale_meters,
                maxPixels=max_pixels,
                bestEffort=best_effort,
            ).get(NO2_BAND)
            valid_pixel_count = daily_image.mask().reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=geometry,
                scale=scale_meters,
                maxPixels=max_pixels,
                bestEffort=best_effort,
            ).get(NO2_BAND)
            coverage_fraction = ee.Number(
                ee.Algorithms.If(
                    ee.Number(total_pixel_count).gt(0),
                    ee.Number(valid_pixel_count).divide(
                        ee.Number(total_pixel_count)
                        ),
                    0,
                )
            )
            return ee.Dictionary(
                {
                    "date": day_start.format("YYYY-MM-dd"),
                    "no2_value": no2_value,
                    "n_images": n_images,
                    "valid_pixel_count": valid_pixel_count,
                    "total_pixel_count": total_pixel_count,
                    "coverage_fraction": coverage_fraction,
                }
            )

        def without_data() -> ee.Dictionary:
            return ee.Dictionary(
                {
                    "date": day_start.format("YYYY-MM-dd"),
                    "no2_value": None,
                    "n_images": 0,
                    "valid_pixel_count": 0,
                    "total_pixel_count": total_pixel_count,
                    "coverage_fraction": 0,
                }
            )

        properties = ee.Dictionary(ee.Algorithms.If(n_images.gt(0),
                                                    with_data(),
                                                    without_data()))
        return ee.Feature(None, properties)

    daily_fc = ee.FeatureCollection(day_offsets.map(build_day_feature))
    info = daily_fc.getInfo()
    if not isinstance(info, dict):
        return pd.DataFrame(
            columns=[
                "date",
                "no2_value",
                "n_images",
                "valid_pixel_count",
                "total_pixel_count",
                "coverage_fraction",
            ]
        )

    raw_features = info.get("features")
    if not isinstance(raw_features, list):
        return pd.DataFrame(
            columns=[
                "date",
                "no2_value",
                "n_images",
                "valid_pixel_count",
                "total_pixel_count",
                "coverage_fraction",
            ]
        )

    rows: list[dict[str, Any]] = []
    for feature in raw_features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if isinstance(properties, dict):
            rows.append(properties)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "no2_value",
                "n_images",
                "valid_pixel_count",
                "total_pixel_count",
                "coverage_fraction",
            ]
        )
    return df.sort_values("date").reset_index(drop=True)
