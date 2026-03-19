"""Earth Engine provider for Sentinel-2 Harmonized spectral data."""

from __future__ import annotations

from datetime import date, datetime

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.providers.s2_registry import (
    S2_COLLECTION_ID,
    get_s2_index_config,
)


def _mask_s2_clouds(image: ee.Image) -> ee.Image:
    """Mask clouds and cirrus using the QA60 bitmask band.

    Bit 10 → opaque clouds, bit 11 → cirrus.
    """
    qa = image.select("QA60")
    cloud_bit_mask = 1 << 10
    cirrus_bit_mask = 1 << 11
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(
        qa.bitwiseAnd(cirrus_bit_mask).eq(0),
    )
    return image.updateMask(mask)


def _to_reflectance(image: ee.Image) -> ee.Image:
    """Scale S2 L1C DN values to [0, 1] reflectance."""
    return image.divide(10_000)


def _compute_index(image: ee.Image, config) -> ee.Image:
    """Compute a spectral index from an expression, or select a raw band."""
    if config.expression is None:
        # Raw band – already selected, just return it.
        return image.select(config.bands).rename(config.key)

    # Build the band-reference map expected by ee.Image.expression().
    band_map = {b: image.select(b) for b in config.bands}
    return image.expression(config.expression, band_map).rename(config.key)


def get_s2_collection(
    index_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    cloud_max: int = 20,
) -> ee.ImageCollection:
    """Return an S2 index ImageCollection filtered by ROI and dates.

    Parameters
    ----------
    index_key:
        Registry key, e.g. ``"NDVI"``, ``"B11"``.
    geometry:
        Earth Engine geometry for spatial filtering.
    start_date, end_date:
        Temporal window (inclusive start, exclusive end).
    cloud_max:
        Maximum ``CLOUDY_PIXEL_PERCENTAGE`` metadata value (0–100).
        Images above this threshold are dropped before cloud masking.
    """
    config = get_s2_index_config(index_key)
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    # All bands we need: the source bands + QA60 for cloud masking.
    select_bands = list(config.bands) + ["QA60"]

    return (
        ee.ImageCollection(S2_COLLECTION_ID)
        .filterDate(start, end)
        .filterBounds(geometry)
        .filter(
            ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cloud_max),
        )
        .select(select_bands)
        .map(_mask_s2_clouds)
        .map(_to_reflectance)
        .map(lambda img: _compute_index(img, config))
    )
