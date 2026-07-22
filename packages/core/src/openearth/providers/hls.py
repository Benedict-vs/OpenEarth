"""Earth Engine provider for Harmonized Landsat Sentinel-2 (HLS v2.0).

Merges HLSL30 (Landsat 8/9) and HLSS30 (Sentinel-2) into one 30 m collection.
Each sensor's bands are renamed to the canonical scheme (RED/GREEN/BLUE/NIR/
SWIR1/SWIR2) so the shared optical products (RGB/NDVI/NDWI) compute uniformly
across both. Clouds are masked from the ``Fmask`` bit band (bits 1|2|3 = cloud,
adjacent-to-cloud/shadow, cloud shadow); snow/water bits are landscape, not
defects, so they are kept.

GEE delivers HLS reflectance as pre-scaled floats in ~[0, 1] (Stage 0 spike —
docs/phase10-execution-plan.md), so NO reflectance scaling is applied; the native
per-band ``B*_scale`` metadata is deliberately ignored (applying it would wash
every frame to black).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.builtin.hls import HLSL30_COLLECTION_ID, HLSS30_COLLECTION_ID
from openearth.catalog.registry import get_product
from openearth.providers.generic import _compute_product
from openearth.providers.qa import bit_mask

if TYPE_CHECKING:
    from datetime import date, datetime

    from openearth.geometry import ROI

# Fmask bits: 0 cirrus (reserved/unused), 1 cloud, 2 adjacent to cloud/shadow,
# 3 cloud shadow, 4 snow/ice, 5 water, 6-7 aerosol level. Cloud mask = 1|2|3.
FMASK_BAND = "Fmask"
FMASK_CLOUD_BITS: tuple[int, ...] = (1, 2, 3)

# Per-sensor native band → canonical name. L30 (Landsat 8/9) has no B8/B12 and
# uses B5 as the narrow NIR; S30 (Sentinel-2) uses B8A. RGB = B4/B3/B2 on both.
L30_BAND_MAP: dict[str, str] = {
    "B4": "RED",
    "B3": "GREEN",
    "B2": "BLUE",
    "B5": "NIR",
    "B6": "SWIR1",
    "B7": "SWIR2",
}
S30_BAND_MAP: dict[str, str] = {
    "B4": "RED",
    "B3": "GREEN",
    "B2": "BLUE",
    "B8A": "NIR",
    "B11": "SWIR1",
    "B12": "SWIR2",
}


def _prep_sensor(
    collection_id: str,
    band_map: dict[str, str],
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Filter one HLS sensor, Fmask-mask clouds, rename bands to canonical.

    No reflectance scaling (GEE HLS is pre-scaled float — Stage 0 spike).
    """
    src_bands = list(band_map)
    canonical = [band_map[b] for b in src_bands]

    def _prep(image: ee.Image) -> ee.Image:
        clear = image.select(FMASK_BAND).bitwiseAnd(bit_mask(FMASK_CLOUD_BITS)).eq(0)
        optical = image.select(src_bands, canonical)
        return ee.Image(optical.updateMask(clear).copyProperties(image, ["system:time_start"]))

    return (
        ee.ImageCollection(collection_id)
        .filterDate(to_ee_date(start_date), to_ee_date(end_date))
        .filterBounds(roi.to_ee_geometry())
        .map(_prep)
    )


def get_hls_collection(
    product_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Return the merged, cloud-masked HLS product collection for *product_key*.

    L30 + S30 are prepared to canonical bands, merged, sorted by acquisition
    time, then the canonical product recipe (RGB/NDVI/NDWI) is applied per image.
    """
    config = get_product("hls", product_key)
    l30 = _prep_sensor(HLSL30_COLLECTION_ID, L30_BAND_MAP, roi, start_date, end_date)
    s30 = _prep_sensor(HLSS30_COLLECTION_ID, S30_BAND_MAP, roi, start_date, end_date)
    merged = ee.ImageCollection(l30.merge(s30)).sort("system:time_start")
    return merged.map(lambda image: ee.Image(_compute_product(image, config)))
