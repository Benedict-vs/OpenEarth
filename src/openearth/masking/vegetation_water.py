"""Vegetation and water masking using NDVI / NDWI from Sentinel-2."""

from __future__ import annotations

from datetime import date, datetime

import ee

from openearth.providers.gee_s2 import _get_s2_base_collection


def apply_vegetation_water_mask(
    image: ee.Image,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    ndvi_threshold: float = 0.3,
    ndwi_threshold: float = 0.0,
    mask_vegetation: bool = True,
    mask_water: bool = True,
) -> ee.Image:
    """Mask vegetation and/or water pixels from *image*.

    Builds a mean Sentinel-2 composite for NDVI and NDWI over the
    same ROI and date range, then masks pixels exceeding the given
    thresholds.  Masked pixels become transparent on tile layers.

    Parameters
    ----------
    image:
        The ee.Image to mask (any source — S2 index, S5P, etc.).
    geometry:
        ROI used for the S2 composite that computes NDVI/NDWI.
    start_date, end_date:
        Temporal window for the S2 reference composite.
    ndvi_threshold:
        Pixels with NDVI > this value are considered vegetation.
    ndwi_threshold:
        Pixels with NDWI > this value are considered water.
    mask_vegetation:
        Whether to mask vegetation pixels.
    mask_water:
        Whether to mask water pixels.

    Returns
    -------
    ee.Image
        The input image with an additional mask applied.
    """
    if not mask_vegetation and not mask_water:
        return image

    base = _get_s2_base_collection(
        geometry, start_date, end_date,
    )
    composite = base.select(["B3", "B4", "B8"]).mean()

    b8 = composite.select("B8")
    b4 = composite.select("B4")
    b3 = composite.select("B3")

    # Start with all-valid mask.
    mask = ee.Image.constant(1)

    if mask_vegetation:
        ndvi = b8.subtract(b4).divide(b8.add(b4))
        mask = mask.And(ndvi.lte(ndvi_threshold))

    if mask_water:
        ndwi = b3.subtract(b8).divide(b3.add(b8))
        mask = mask.And(ndwi.lte(ndwi_threshold))

    return image.updateMask(mask)
