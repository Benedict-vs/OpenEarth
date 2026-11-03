"""Data providers and the key/source dispatcher (v1-compatible surface)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import ee

from openearth.catalog import ProductSpec, resolve_product
from openearth.ee.client import ee_call
from openearth.providers.generic import get_generic_collection
from openearth.providers.hls import get_hls_collection
from openearth.providers.landsat import get_landsat_collection
from openearth.providers.s1 import get_s1_collection
from openearth.providers.s2 import get_s2_collection
from openearth.providers.s5p import get_trace_gas_collection

if TYPE_CHECKING:
    from openearth.geometry import ROI

__all__ = [
    "get_collection",
    "get_compare_image",
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
    dataset_id, config = resolve_product(data_key, source)
    if config.needs_ref:
        raise ValueError(
            f"Product {data_key!r} needs a reference window (needs_ref); it renders "
            "only through the two-window compare pipeline (get_compare_image), never a "
            "single-window collection."
        )
    if dataset_id == "s1":
        return get_s1_collection(data_key, roi, start_date, end_date)
    if dataset_id == "s2":
        return get_s2_collection(data_key, roi, start_date, end_date)
    if dataset_id == "s5p":
        return get_trace_gas_collection(data_key, roi, start_date, end_date)
    if dataset_id == "hls":
        return get_hls_collection(data_key, roi, start_date, end_date)
    if dataset_id == "landsat":
        return get_landsat_collection(data_key, roi, start_date, end_date)
    return get_generic_collection(dataset_id, data_key, roi, start_date, end_date)


def get_compare_image(
    data_key: str,
    roi: ROI,
    ref_start: str | date | datetime,
    ref_end: str | date | datetime,
    start: str | date | datetime,
    end: str | date | datetime,
    source: str,
) -> ee.Image:
    """Render a two-window compare product (``needs_ref``) → one image.

    Builds a masked mean composite of each raw input band over the reference window
    (``pre_``) and the request window (``post_``) — reusing the per-source pipeline
    via :func:`get_collection`, so cloud masking / polarisation handling are
    inherited — then applies the product's ``pre_``/``post_`` expression.
    """
    dataset_id, config = resolve_product(data_key, source)
    if not config.needs_ref:
        raise ValueError(f"Product {data_key!r} is not a two-window compare product.")
    if not config.expression or not config.bands:
        raise ValueError(f"Compare product {data_key!r} needs an expression over input bands.")

    def _window_band(band: str, s: str | date | datetime, e: str | date | datetime) -> ee.Image:
        band_out = get_product_config(band, dataset_id).band
        return get_collection(band, roi, s, e, dataset_id).mean().select(band_out)

    combined: ee.Image | None = None
    band_map: dict[str, ee.Image] = {}
    for band in config.bands:
        for prefix, s, e in (("pre", ref_start, ref_end), ("post", start, end)):
            name = f"{prefix}_{band}"
            renamed = _window_band(band, s, e).rename(name)
            combined = renamed if combined is None else combined.addBands(renamed)
            band_map[name] = renamed
    assert combined is not None  # bands is non-empty (checked above)

    result = combined.expression(config.expression, band_map).rename(config.key)
    if roi.is_global:
        return result
    return result.clip(roi.to_ee_geometry())


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
