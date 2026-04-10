"""Configuration registry for Sentinel-2 Harmonized spectral indices."""

from __future__ import annotations

from dataclasses import dataclass, field

# Green → dark-green vegetation palette.
VEGETATION_PALETTE: list[str] = [
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

# Blue → brown water palette.
WATER_PALETTE: list[str] = [
    "#a52a2a",
    "#d2691e",
    "#daa520",
    "#f0e68c",
    "#ffffbf",
    "#b0e0e6",
    "#87ceeb",
    "#4682b4",
    "#1e90ff",
    "#00008b",
]

# Diverging blue → white → red palette for methane proxies.
METHANE_PALETTE: list[str] = [
    "#313695",
    "#4575b4",
    "#74add1",
    "#abd9e9",
    "#e0f3f8",
    "#ffffbf",
    "#fee090",
    "#fdae61",
    "#f46d43",
    "#d73027",
]

# Grey → white reflectance palette (for raw SWIR bands).
SWIR_PALETTE: list[str] = [
    "#000004",
    "#1b0c41",
    "#4a0c6b",
    "#781c6d",
    "#a52c60",
    "#cf4446",
    "#ed6925",
    "#fb9b06",
    "#f7d13d",
    "#fcffa4",
]

S2_COLLECTION_ID = "COPERNICUS/S2_HARMONIZED"
S2_SR_COLLECTION_ID = "COPERNICUS/S2_SR_HARMONIZED"


@dataclass(frozen=True)
class S2IndexConfig:
    """Immutable descriptor for a Sentinel-2 spectral index or band."""

    key: str
    name: str
    bands: list[str]
    expression: str | None
    vis_min: float
    vis_max: float
    valid_min: float
    valid_max: float
    display_unit: str
    display_scale: float = 1.0
    palette: list[str] = field(
        default_factory=lambda: list(VEGETATION_PALETTE),
    )
    is_rgb: bool = False
    collection_id: str | None = None
    methane_only: bool = False

    @property
    def band(self) -> str:
        """Output band name (compatible with GasConfig)."""
        return self.key


S2_REGISTRY: dict[str, S2IndexConfig] = {
    # ── Composites & spectral indices ────────────────────────────
    "RGB": S2IndexConfig(
        key="RGB",
        name="True Colour (B4/B3/B2)",
        bands=["B4", "B3", "B2"],
        expression=None,
        vis_min=0.0,
        vis_max=0.3,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        is_rgb=True,
        collection_id=S2_SR_COLLECTION_ID,
    ),
    "NDVI": S2IndexConfig(
        key="NDVI",
        name="Normalized Difference Vegetation Index",
        bands=["B8", "B4"],
        expression="(B8 - B4) / (B8 + B4)",
        vis_min=-0.2,
        vis_max=0.9,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        palette=VEGETATION_PALETTE,
    ),
    "NDWI": S2IndexConfig(
        key="NDWI",
        name="Normalized Difference Water Index",
        bands=["B3", "B8"],
        expression="(B3 - B8) / (B3 + B8)",
        vis_min=-0.8,
        vis_max=0.8,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        palette=WATER_PALETTE,
    ),
    "EVI": S2IndexConfig(
        key="EVI",
        name="Enhanced Vegetation Index",
        bands=["B8", "B4", "B2"],
        expression="2.5 * (B8 - B4) / (B8 + 6.0 * B4 - 7.5 * B2 + 1.0)",
        vis_min=-0.2,
        vis_max=0.8,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        palette=VEGETATION_PALETTE,
    ),
    "MBSP": S2IndexConfig(
        key="MBSP",
        name="Multi-Band Single-Pass CH₄ proxy",
        bands=["B12", "B11"],
        expression="(B12 - B11) / B11",
        vis_min=-0.5,
        vis_max=0.1,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="ratio",
        palette=METHANE_PALETTE,
        methane_only=True,
    ),
    "B12_B11": S2IndexConfig(
        key="B12_B11",
        name="SWIR Band Ratio (B12/B11, CH₄ proxy)",
        bands=["B12", "B11"],
        expression="B12 / B11",
        vis_min=0.3,
        vis_max=0.9,
        valid_min=0.0,
        valid_max=2.0,
        display_unit="ratio",
        palette=METHANE_PALETTE,
        methane_only=True,
    ),
    "CH4_ANOMALY": S2IndexConfig(
        key="CH4_ANOMALY",
        name="Methane Enhancement (B12/B11 anomaly)",
        bands=["B12", "B11"],
        expression="B12 / B11",
        vis_min=-0.08,
        vis_max=0.02,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="delta ratio",
        palette=METHANE_PALETTE,
        methane_only=True,
    ),
    # ── Raw spectral bands (ordered by wavelength) ───────────────
    "B1": S2IndexConfig(
        key="B1",
        name="Coastal Aerosol (443 nm, 60 m)",
        bands=["B1"],
        expression=None,
        vis_min=0.0,
        vis_max=0.25,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B2": S2IndexConfig(
        key="B2",
        name="Blue (490 nm, 10 m)",
        bands=["B2"],
        expression=None,
        vis_min=0.0,
        vis_max=0.3,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B3": S2IndexConfig(
        key="B3",
        name="Green (560 nm, 10 m)",
        bands=["B3"],
        expression=None,
        vis_min=0.0,
        vis_max=0.3,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B4": S2IndexConfig(
        key="B4",
        name="Red (665 nm, 10 m)",
        bands=["B4"],
        expression=None,
        vis_min=0.0,
        vis_max=0.3,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B5": S2IndexConfig(
        key="B5",
        name="Red Edge 1 (705 nm, 20 m)",
        bands=["B5"],
        expression=None,
        vis_min=0.0,
        vis_max=0.4,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B6": S2IndexConfig(
        key="B6",
        name="Red Edge 2 (740 nm, 20 m)",
        bands=["B6"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B7": S2IndexConfig(
        key="B7",
        name="Red Edge 3 (783 nm, 20 m)",
        bands=["B7"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B8": S2IndexConfig(
        key="B8",
        name="NIR Broad (842 nm, 10 m)",
        bands=["B8"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B8A": S2IndexConfig(
        key="B8A",
        name="NIR Narrow (865 nm, 20 m)",
        bands=["B8A"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B9": S2IndexConfig(
        key="B9",
        name="Water Vapour (945 nm, 60 m)",
        bands=["B9"],
        expression=None,
        vis_min=0.0,
        vis_max=0.3,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B10": S2IndexConfig(
        key="B10",
        name="SWIR Cirrus (1375 nm, 60 m)",
        bands=["B10"],
        expression=None,
        vis_min=0.0,
        vis_max=0.05,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B11": S2IndexConfig(
        key="B11",
        name="SWIR-1 (1610 nm, 20 m)",
        bands=["B11"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B12": S2IndexConfig(
        key="B12",
        name="SWIR-2 (2190 nm, 20 m)",
        bands=["B12"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
}


METHANE_S2_KEYS: list[str] = [
    k for k, v in S2_REGISTRY.items() if v.methane_only
]


def get_s2_index_config(key: str) -> S2IndexConfig:
    """Look up a Sentinel-2 index configuration by key."""
    try:
        return S2_REGISTRY[key]
    except KeyError:
        valid = ", ".join(sorted(S2_REGISTRY))
        raise ValueError(
            f"Unknown S2 index key {key!r}. "
            f"Valid keys: {valid}"
        ) from None
