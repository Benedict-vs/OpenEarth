"""Built-in Sentinel-1 SAR GRD products.

Ported from the v1 registry with honest naming: "VV_VH_RATIO" is a
dB difference (VV − VH), not a linear ratio, and is labeled as such.
The v2 provider adds orbit-pass filtering and optional speckle reduction.
"""

from __future__ import annotations

from openearth.catalog.models import DatasetSpec, ProductSpec

# ------------------------------------------------------------------
# Palettes
# ------------------------------------------------------------------

# Grey-scale for raw SAR backscatter (VV, VH).
_PALETTE_SAR: list[str] = [
    "#000000",
    "#1c1c1c",
    "#383838",
    "#555555",
    "#717171",
    "#8d8d8d",
    "#aaaaaa",
    "#c6c6c6",
    "#e2e2e2",
    "#ffffff",
]

# Diverging blue → white → red for VV/VH ratio.
_PALETTE_RATIO: list[str] = [
    "#2166ac",
    "#4393c3",
    "#92c5de",
    "#d1e5f0",
    "#f7f7f7",
    "#fddbc7",
    "#f4a582",
    "#d6604d",
    "#b2182b",
    "#67001f",
]

# Green sequential for Radar Vegetation Index.
_PALETTE_RVI: list[str] = [
    "#d73027",
    "#f46d43",
    "#fdae61",
    "#fee08b",
    "#ffffbf",
    "#d9ef8b",
    "#a6d96a",
    "#66bd63",
    "#1a9850",
    "#006837",
]

S1_COLLECTION_ID = "COPERNICUS/S1_GRD"


S1_PRODUCTS: dict[str, ProductSpec] = {
    "VV": ProductSpec(
        key="VV",
        name="Backscatter (co-pol)",
        collection_id=S1_COLLECTION_ID,
        vis_min=-25.0,
        vis_max=0.0,
        valid_min=-50.0,
        valid_max=10.0,
        display_unit="dB",
        description=(
            "**Reading the VV backscatter scale:** "
            "VV co-polarized radar backscatter "
            "in dB. "
            "**High values (bright)** near 0 dB "
            "indicate strong returns from urban "
            "areas, rough water, or steep terrain. "
            "**Mid-range values** (\u221215 to "
            "\u22125 dB) are typical of vegetated "
            "land and cropland. "
            "**Low values (dark)** below \u221220 dB "
            "indicate calm water, smooth surfaces, "
            "or radar shadow."
        ),
        palette=list(_PALETTE_SAR),
    ),
    "VH": ProductSpec(
        key="VH",
        name="Backscatter (cross-pol)",
        collection_id=S1_COLLECTION_ID,
        vis_min=-30.0,
        vis_max=-5.0,
        valid_min=-50.0,
        valid_max=5.0,
        display_unit="dB",
        description=(
            "**Reading the VH backscatter scale:** "
            "VH cross-polarized radar backscatter "
            "in dB. "
            "**Higher values (brighter)** indicate "
            "strong volume scattering from dense "
            "vegetation canopies or rough terrain. "
            "**Mid-range values** (\u221220 to "
            "\u221210 dB) are typical of crops and "
            "mixed land cover. "
            "**Low values (dark)** below \u221225 dB "
            "indicate smooth surfaces such as "
            "calm water, bare soil, or urban areas "
            "with minimal cross-pol return."
        ),
        palette=list(_PALETTE_SAR),
    ),
    "VV_VH_RATIO": ProductSpec(
        key="VV_VH_RATIO",
        name="Polarization Difference (VV − VH, dB)",
        collection_id=S1_COLLECTION_ID,
        vis_min=0.0,
        vis_max=15.0,
        valid_min=-30.0,
        valid_max=30.0,
        display_unit="dB",
        description=(
            "**Reading the polarization difference scale:** "
            "This shows the difference VV \u2212 VH "
            "in dB, equivalent to the log of the "
            "linear power ratio VV\u2097\u1d35\u2099 / "
            "VH\u2097\u1d35\u2099. "
            "**High values (red, 10\u201315 dB)** "
            "indicate VV dominates \u2014 typical of "
            "calm water, bare soil, or urban "
            "structures with strong specular or "
            "double-bounce returns. "
            "**Mid-range values (5\u201310 dB)** are "
            "typical of cropland and mixed "
            "land cover. "
            "**Low values (blue, near 0 dB)** "
            "indicate strong depolarisation "
            "\u2014 typical of dense vegetation "
            "with significant volume scattering. "
            "Useful for land-cover discrimination "
            "independent of absolute backscatter "
            "intensity."
        ),
        palette=list(_PALETTE_RATIO),
    ),
    "RVI": ProductSpec(
        key="RVI",
        name="Radar Vegetation Index",
        collection_id=S1_COLLECTION_ID,
        vis_min=0.0,
        vis_max=1.0,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="",
        description=(
            "**Reading the Radar Vegetation Index:** "
            "RVI = 4 \u00b7 VH\u2097\u1d35\u2099 / "
            "(VV\u2097\u1d35\u2099 + VH\u2097\u1d35\u2099), "
            "where linear power is derived from the "
            "dB backscatter. "
            "**Values near 0** indicate bare soil, "
            "open water, or built surfaces where "
            "VH cross-polarisation is weak. "
            "**Values near 1** indicate dense "
            "vegetation canopies that strongly "
            "depolarise the radar signal. "
            "Unlike optical vegetation indices, "
            "RVI is unaffected by clouds or smoke "
            "and works in all weather conditions."
        ),
        palette=list(_PALETTE_RVI),
    ),
}


S1_DATASET = DatasetSpec(
    id="s1",
    title="Sentinel-1 SAR GRD",
    collection_id=S1_COLLECTION_ID,
    attribution="Google Earth Engine / Copernicus Sentinel-1",
    default_scale_m=100,
    products=S1_PRODUCTS,
)
