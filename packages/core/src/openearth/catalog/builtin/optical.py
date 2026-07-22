"""Canonical optical products shared by the HLS and Landsat datasets (Phase 10).

Both the HLS and Landsat providers rename each sensor's native bands to a common
canonical scheme — ``RED GREEN BLUE NIR SWIR1 SWIR2`` — before compositing, so a
single set of product recipes serves every spacecraft (Sentinel-2, Landsat 5/7,
Landsat 8/9). RGB / NDVI / NDWI here are defined over those canonical names; the
providers guarantee the bands exist. Reflectance scale, vis ranges, and palettes
match the Sentinel-2 equivalents (all surface reflectance) so a source switch is
visually continuous.
"""

from __future__ import annotations

from openearth.catalog.builtin.s2 import VEGETATION_PALETTE, WATER_PALETTE
from openearth.catalog.models import ProductSpec

# The canonical band names every optical provider renames its native bands to.
CANONICAL_BANDS: tuple[str, ...] = ("RED", "GREEN", "BLUE", "NIR", "SWIR1", "SWIR2")


def canonical_optical_products() -> dict[str, ProductSpec]:
    """RGB / NDVI / NDWI over the canonical band scheme (fresh dict per call)."""
    return {
        "RGB": ProductSpec(
            key="RGB",
            name="True Colour (Red/Green/Blue)",
            bands=["RED", "GREEN", "BLUE"],
            expression=None,
            vis_min=0.0,
            vis_max=0.3,
            valid_min=0.0,
            valid_max=1.0,
            display_unit="reflectance",
            is_rgb=True,
        ),
        "NDVI": ProductSpec(
            key="NDVI",
            name="Normalized Difference Vegetation Index",
            bands=["NIR", "RED"],
            expression="(NIR - RED) / (NIR + RED)",
            vis_min=-0.2,
            vis_max=0.9,
            valid_min=-1.0,
            valid_max=1.0,
            display_unit="index",
            description=(
                "**Reading the NDVI scale:** "
                "NDVI measures vegetation greenness using "
                "(NIR − Red) / (NIR + Red). "
                "**Negative values** (red/brown) indicate water, bare soil, or "
                "built surfaces. "
                "**Values near 0** indicate sparse vegetation or dry ground. "
                "**Values 0.2–0.5** indicate shrubs, grass, or crops. "
                "**Values above 0.6** indicate dense, healthy vegetation such "
                "as forests."
            ),
            palette=VEGETATION_PALETTE,
        ),
        "NDWI": ProductSpec(
            key="NDWI",
            name="Normalized Difference Water Index",
            bands=["GREEN", "NIR"],
            expression="(GREEN - NIR) / (GREEN + NIR)",
            vis_min=-0.8,
            vis_max=0.8,
            valid_min=-1.0,
            valid_max=1.0,
            display_unit="index",
            description=(
                "**Reading the NDWI scale:** "
                "NDWI highlights water using (Green − NIR) / (Green + NIR). "
                "**Positive values** (blue) indicate open water surfaces. "
                "**Values near 0** indicate moist soil or the water–land "
                "boundary. "
                "**Negative values** (brown) indicate dry land, vegetation, or "
                "built surfaces."
            ),
            palette=WATER_PALETTE,
        ),
    }
