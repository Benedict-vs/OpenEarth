"""Google Earth Engine provider for Sentinel-5P tropospheric NO2."""

from __future__ import annotations

from datetime import date, datetime

import ee
from openearth.analytics.conversions import to_ee_date

NO2_DATASET_ID = "COPERNICUS/S5P/OFFL/L3_NO2"
NO2_BAND = "tropospheric_NO2_column_number_density"
# Tropospheric column focuses on the lower atmosphere where emissions
# and human exposure happen rather then total which includes
# stratosphere and adds unwanted background information


def get_no2_collection(
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Return NO2 ImageCollection for ROI and date range.
    """
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    return (
        ee.ImageCollection(NO2_DATASET_ID)
        .filterDate(start, end)
        .filterBounds(geometry)
        .select(NO2_BAND)
    )
