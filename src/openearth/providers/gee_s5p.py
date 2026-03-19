"""Earth Engine provider for Sentinel-5P trace gases."""

from __future__ import annotations

from datetime import date, datetime

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.providers.s5p_registry import get_gas_config


def get_trace_gas_collection(
    gas_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Return an ImageCollection for *gas_key* filtered by ROI and dates."""
    config = get_gas_config(gas_key)
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    return (
        ee.ImageCollection(config.collection_id)
        .filterDate(start, end)
        .filterBounds(geometry)
        .select(config.band)
    )
