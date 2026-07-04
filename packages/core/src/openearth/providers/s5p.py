"""Earth Engine provider for Sentinel-5P trace gases.

v2 change vs the v1 provider: **per-gas valid-range masking**. The GEE
COPERNICUS/S5P/OFFL/L3_* grids are QA-screened upstream (harpconvert
``qa_value`` filtering per product), but retrieval artifacts outside each
product's physically valid range still occur — v1 rendered them as extreme
false colors. Values outside ``[valid_min, valid_max]`` are now masked.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.registry import get_product

if TYPE_CHECKING:
    from openearth.geometry import ROI


def get_trace_gas_collection(
    gas_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    mask_invalid: bool = True,
) -> ee.ImageCollection:
    """Return an ImageCollection for *gas_key* filtered by ROI and dates."""
    config = get_product("s5p", gas_key)
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    collection = (
        ee.ImageCollection(config.collection_id)
        .filterDate(start, end)
        .filterBounds(roi.to_ee_geometry())
        .select(config.band)
    )

    if mask_invalid:
        lo, hi = config.valid_min, config.valid_max

        def _mask(image: ee.Image) -> ee.Image:
            valid = image.gte(lo).And(image.lte(hi))
            return image.updateMask(valid)

        collection = collection.map(_mask)

    return collection
