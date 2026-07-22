"""Built-in Landsat Collection 2 Level-2 dataset — deep history to 1984 (Phase 10).

Merges the four Landsat C2 L2 surface-reflectance archives per requested window:
LT05 (1984–2012), LE07 (1999–2024), LC08 (2013–), LC09 (2021–), all 30 m. The RGB
band NUMBERING SHIFTS between L5/7 and L8/9 (a thermal band was inserted), so the
provider maps per-spacecraft to the canonical scheme; product keys stay uniform.
SR values need the ``×0.0000275 − 0.2`` scale; QA_PIXEL carries the cloud bits.

Not a fallback for HLS — this is a separately selectable deep-history source. Post
-2003 Landsat-7 has SLC-off wedge gaps and is composite-only material (see
``providers/landsat.slc_off_advisory``).
"""

from __future__ import annotations

from openearth.catalog.builtin.optical import canonical_optical_products
from openearth.catalog.models import DatasetSpec

LT05_COLLECTION_ID = "LANDSAT/LT05/C02/T1_L2"
LE07_COLLECTION_ID = "LANDSAT/LE07/C02/T1_L2"
LC08_COLLECTION_ID = "LANDSAT/LC08/C02/T1_L2"
LC09_COLLECTION_ID = "LANDSAT/LC09/C02/T1_L2"

LANDSAT_PRODUCTS = canonical_optical_products()

LANDSAT_DATASET = DatasetSpec(
    id="landsat",
    title="Landsat Collection 2 Level-2 (1984–present)",
    # Representative id only — the provider merges LT05/LE07/LC08/LC09 explicitly.
    collection_id=LC08_COLLECTION_ID,
    attribution="USGS / NASA Landsat Collection 2",
    default_scale_m=30,
    products=LANDSAT_PRODUCTS,
)
