"""Generic Earth Engine provider for user-registered (TOML) datasets.

Builds a filtered ImageCollection for any dataset the catalog knows about,
using only what a :class:`~openearth.catalog.models.ProductSpec` declares:
raw-band select, band-math ``expression``, RGB band stacks, and valid-range
masking. This is the runtime half of "one new dataset = zero new code" —
the built-in providers keep their sensor-specific pipelines (cloud masking,
orbit filters, QA), user datasets get this honest baseline.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.registry import get_dataset

if TYPE_CHECKING:
    from openearth.catalog.models import ProductSpec
    from openearth.geometry import ROI


def _compute_product(image: ee.Image, config: ProductSpec) -> ee.Image:
    """Select or compute *config* from one image, preserving its timestamp."""
    if config.builder is not None:
        raise ValueError(
            f"Product {config.key!r} requires the dedicated builder "
            f"{config.builder!r} and cannot be computed generically."
        )

    if config.is_rgb:
        return image.select(config.bands)

    if config.expression is not None:
        band_map = {b: image.select(b) for b in config.bands or []}
        return (
            image.expression(config.expression, band_map)
            .rename(config.key)
            .copyProperties(image, ["system:time_start"])
        )

    return image.select(config.band)


def _mask_valid_range(image: ee.Image, config: ProductSpec) -> ee.Image:
    valid = image.gte(config.valid_min).And(image.lte(config.valid_max))
    return image.updateMask(valid)


def get_generic_collection(
    dataset_id: str,
    product_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    mask_invalid: bool = True,
) -> ee.ImageCollection:
    """Return the filtered ImageCollection for any catalog dataset/product."""
    dataset = get_dataset(dataset_id)
    config = dataset.get(product_key)
    if config.builder is not None:
        raise ValueError(
            f"Product {config.key!r} requires the dedicated builder "
            f"{config.builder!r} and cannot be computed generically."
        )

    collection = (
        ee.ImageCollection(config.collection_id or dataset.collection_id)
        .filterDate(to_ee_date(start_date), to_ee_date(end_date))
        .filterBounds(roi.to_ee_geometry())
        .map(lambda img: ee.Image(_compute_product(img, config)))
    )

    if mask_invalid and not config.is_rgb:
        collection = collection.map(lambda img: _mask_valid_range(img, config))

    return collection
