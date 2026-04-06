"""Configuration registry for Sentinel-1 SAR GRD variables."""

from __future__ import annotations

from dataclasses import dataclass, field

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


@dataclass(frozen=True)
class S1BandConfig:
    """Immutable descriptor for a Sentinel-1 derived variable."""

    key: str
    name: str
    collection_id: str
    vis_min: float
    vis_max: float
    valid_min: float
    valid_max: float
    display_unit: str
    display_scale: float = 1.0
    palette: list[str] = field(
        default_factory=lambda: list(_PALETTE_SAR),
    )

    @property
    def band(self) -> str:
        """Output band name (compatible with GasConfig / S2IndexConfig)."""
        return self.key


S1_REGISTRY: dict[str, S1BandConfig] = {
    "VV": S1BandConfig(
        key="VV",
        name="VV Polarization",
        collection_id=S1_COLLECTION_ID,
        vis_min=-25.0,
        vis_max=0.0,
        valid_min=-50.0,
        valid_max=10.0,
        display_unit="dB",
        palette=list(_PALETTE_SAR),
    ),
    "VH": S1BandConfig(
        key="VH",
        name="VH Polarization",
        collection_id=S1_COLLECTION_ID,
        vis_min=-30.0,
        vis_max=-5.0,
        valid_min=-50.0,
        valid_max=5.0,
        display_unit="dB",
        palette=list(_PALETTE_SAR),
    ),
    "VV_VH_RATIO": S1BandConfig(
        key="VV_VH_RATIO",
        name="VV/VH Ratio (dB difference)",
        collection_id=S1_COLLECTION_ID,
        vis_min=0.0,
        vis_max=15.0,
        valid_min=-30.0,
        valid_max=30.0,
        display_unit="dB",
        palette=list(_PALETTE_RATIO),
    ),
    "RVI": S1BandConfig(
        key="RVI",
        name="Radar Vegetation Index",
        collection_id=S1_COLLECTION_ID,
        vis_min=0.0,
        vis_max=1.0,
        valid_min=0.0,
        valid_max=1.0,
        display_unit="",
        palette=list(_PALETTE_RVI),
    ),
}


def get_s1_band_config(key: str) -> S1BandConfig:
    """Return the S1BandConfig for *key*.

    Raises
    ------
    ValueError
        If *key* is not in the registry.
    """
    try:
        return S1_REGISTRY[key]
    except KeyError:
        valid = ", ".join(S1_REGISTRY)
        raise ValueError(
            f"Unknown S1 variable {key!r}. "
            f"Valid keys: {valid}"
        ) from None
