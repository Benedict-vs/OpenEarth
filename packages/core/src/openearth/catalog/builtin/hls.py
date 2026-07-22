"""Built-in Harmonized Landsat Sentinel-2 (HLS) dataset — Phase 10.

Merges ``NASA/HLS/HLSL30/v002`` (Landsat 8/9) and ``NASA/HLS/HLSS30/v002``
(Sentinel-2), both 30 m, harmonized to a combined 2–3 day revisit from
2013-04-11 (L30) / 2015-11-28 (S30). Cloud masking is the ``Fmask`` bit band
(cloud/adjacent/shadow); pixels arrive as pre-scaled float reflectance (verified
by the Stage 0 spike — no ×scale is applied). The provider renames each sensor's
bands to the canonical scheme so RGB/NDVI/NDWI are source-uniform.
"""

from __future__ import annotations

from openearth.catalog.builtin.optical import canonical_optical_products
from openearth.catalog.models import DatasetSpec

HLSL30_COLLECTION_ID = "NASA/HLS/HLSL30/v002"
HLSS30_COLLECTION_ID = "NASA/HLS/HLSS30/v002"

HLS_PRODUCTS = canonical_optical_products()

HLS_DATASET = DatasetSpec(
    id="hls",
    title="Harmonized Landsat Sentinel-2 (HLS v2.0)",
    # Representative id only — the provider merges L30 + S30 explicitly and never
    # reads this. Kept for the catalog contract (DatasetSpec requires one).
    collection_id=HLSS30_COLLECTION_ID,
    attribution="NASA LP DAAC / Harmonized Landsat Sentinel-2",
    default_scale_m=30,
    products=HLS_PRODUCTS,
)
