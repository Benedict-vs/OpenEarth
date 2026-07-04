"""Vegetation and water masking using NDVI / NDWI from Sentinel-2.

Ported from v1 ``masking/vegetation_water.py``. The NDVI/NDWI reference
composite uses L2A surface reflectance (v2 default); the thresholds are
robust to the L1C→L2A switch at these index magnitudes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import ee

from openearth.providers.s2 import get_s2_base_collection

if TYPE_CHECKING:
    from openearth.geometry import ROI


def apply_vegetation_water_mask(
    image: ee.Image,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    ndvi_threshold: float = 0.3,
    ndwi_threshold: float = 0.0,
    mask_vegetation: bool = True,
    mask_water: bool = True,
) -> ee.Image:
    """Mask vegetation and/or water pixels from *image*.

    Builds a mean Sentinel-2 composite for NDVI and NDWI over the same ROI
    and date range, then masks pixels exceeding the given thresholds.
    Masked pixels become transparent on tile layers.
    """
    if not mask_vegetation and not mask_water:
        return image

    base = get_s2_base_collection(roi, start_date, end_date)
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
