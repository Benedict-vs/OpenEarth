"""Data providers and the key/source dispatcher (v1-compatible surface)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import ee

from openearth.catalog import ProductSpec, resolve_product
from openearth.catalog.registry import resolve_source
from openearth.ee.client import ee_call
from openearth.providers.generic import get_generic_collection
from openearth.providers.s1 import get_s1_collection
from openearth.providers.s2 import get_s2_collection
from openearth.providers.s5p import get_trace_gas_collection

if TYPE_CHECKING:
    from openearth.geometry import ROI

__all__ = [
    "get_collection",
    "get_product_config",
    "get_single_image",
    "list_acquisition_times",
]


def get_product_config(data_key: str, source: str) -> ProductSpec:
    """Return the catalog product for *data_key* (honors the "methane" sentinel)."""
    return resolve_product(data_key, source)[1]


def get_collection(
    data_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str,
) -> ee.ImageCollection:
    """Return the filtered ImageCollection for *source*.

    Built-in dataset ids route to their sensor-specific pipelines; any other
    id (user-registered TOML datasets) goes through the generic provider.
    """
    dataset_id = resolve_source(data_key, source)
    if dataset_id == "s1":
        return get_s1_collection(data_key, roi, start_date, end_date)
    if dataset_id == "s2":
        return get_s2_collection(data_key, roi, start_date, end_date)
    if dataset_id == "s5p":
        return get_trace_gas_collection(data_key, roi, start_date, end_date)
    return get_generic_collection(dataset_id, data_key, roi, start_date, end_date)


def list_acquisition_times(
    data_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str,
) -> list[datetime]:
    """Return sorted UTC datetimes for every image in the collection."""
    collection = get_collection(data_key, roi, start_date, end_date, source)
    timestamps_ms = ee_call(collection.aggregate_array("system:time_start").getInfo) or []
    if not timestamps_ms:
        return []
    return sorted(datetime.fromtimestamp(ms / 1000, tz=UTC) for ms in timestamps_ms)


def get_single_image(
    data_key: str,
    roi: ROI,
    timestamp_ms: int,
    source: str,
) -> ee.Image:
    """Return one image matching *timestamp_ms* exactly."""
    # Build a 1-day window to keep the collection query efficient.
    centre = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    start = (centre - timedelta(hours=12)).isoformat()
    end = (centre + timedelta(hours=12)).isoformat()

    collection = get_collection(data_key, roi, start, end, source)
    return collection.filter(ee.Filter.eq("system:time_start", timestamp_ms)).first()
