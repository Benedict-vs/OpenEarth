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


@dataclass(frozen=True)
class S2IndexConfig:
    """Immutable descriptor for a Sentinel-2 spectral index or band."""

    key: str
    name: str
    bands: list[str]
    expression: str | None
    vis_min: float
    vis_max: float
    display_unit: str
    display_scale: float = 1.0
    palette: list[str] = field(
        default_factory=lambda: list(VEGETATION_PALETTE),
    )

    @property
    def band(self) -> str:
        """Output band name (compatible with GasConfig)."""
        return self.key


S2_REGISTRY: dict[str, S2IndexConfig] = {
    "NDVI": S2IndexConfig(
        key="NDVI",
        name="Normalized Difference Vegetation Index",
        bands=["B8", "B4"],
        expression="(B8 - B4) / (B8 + B4)",
        vis_min=-1.0,
        vis_max=1.0,
        display_unit="index",
        palette=VEGETATION_PALETTE,
    ),
    "NDWI": S2IndexConfig(
        key="NDWI",
        name="Normalized Difference Water Index",
        bands=["B3", "B8"],
        expression="(B3 - B8) / (B3 + B8)",
        vis_min=-1.0,
        vis_max=1.0,
        display_unit="index",
        palette=WATER_PALETTE,
    ),
    "EVI": S2IndexConfig(
        key="EVI",
        name="Enhanced Vegetation Index",
        bands=["B8", "B4", "B2"],
        expression="2.5 * (B8 - B4) / (B8 + 6.0 * B4 - 7.5 * B2 + 1.0)",
        vis_min=-1.0,
        vis_max=1.0,
        display_unit="index",
        palette=VEGETATION_PALETTE,
    ),
    "B11": S2IndexConfig(
        key="B11",
        name="SWIR-1 (1.610 µm)",
        bands=["B11"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
    "B12": S2IndexConfig(
        key="B12",
        name="SWIR-2 (2.190 µm)",
        bands=["B12"],
        expression=None,
        vis_min=0.0,
        vis_max=0.5,
        display_unit="reflectance",
        palette=SWIR_PALETTE,
    ),
}


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
