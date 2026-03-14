"""Central configuration registry for Sentinel-5P trace gases."""

from __future__ import annotations

from dataclasses import dataclass, field

# 10-stop Spectral-derived diverging palette (blue → red).
DEFAULT_PALETTE: list[str] = [
    "#5e4fa2",
    "#3288bd",
    "#66c2a5",
    "#abdda4",
    "#e6f598",
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d53e4f",
    "#9e0142",
]


@dataclass(frozen=True)
class GasConfig:
    """Immutable descriptor for a single trace gas product."""

    key: str
    name: str
    collection_id: str
    band: str
    vis_min: float
    vis_max: float
    display_unit: str
    display_scale: float = 1.0
    palette: list[str] = field(
        default_factory=lambda: list(DEFAULT_PALETTE),
    )


GAS_REGISTRY: dict[str, GasConfig] = {
    "NO2": GasConfig(
        key="NO2",
        name="Nitrogen Dioxide",
        collection_id="COPERNICUS/S5P/OFFL/L3_NO2",
        band="tropospheric_NO2_column_number_density",
        vis_min=0.0,
        vis_max=0.0003,
        display_unit="\u00b5mol/m\u00b2",
        display_scale=1e6,
    ),
    "SO2": GasConfig(
        key="SO2",
        name="Sulphur Dioxide",
        collection_id="COPERNICUS/S5P/OFFL/L3_SO2",
        band="SO2_column_number_density",
        vis_min=0.0,
        vis_max=0.0005,
        display_unit="\u00b5mol/m\u00b2",
        display_scale=1e6,
    ),
    "CO": GasConfig(
        key="CO",
        name="Carbon Monoxide",
        collection_id="COPERNICUS/S5P/OFFL/L3_CO",
        band="CO_column_number_density",
        vis_min=0.0,
        vis_max=0.05,
        display_unit="mmol/m\u00b2",
        display_scale=1e3,
    ),
    "O3": GasConfig(
        key="O3",
        name="Ozone",
        collection_id="COPERNICUS/S5P/OFFL/L3_O3",
        band="O3_column_number_density",
        vis_min=0.08,
        vis_max=0.18,
        display_unit="mol/m\u00b2",
        display_scale=1.0,
    ),
    "CH4": GasConfig(
        key="CH4",
        name="Methane",
        collection_id="COPERNICUS/S5P/OFFL/L3_CH4",
        band=(
            "CH4_column_volume_mixing_ratio"
            "_dry_air_bias_corrected"
        ),
        vis_min=1750.0,
        vis_max=2000.0,
        display_unit="ppb",
        display_scale=1.0,
    ),
    "HCHO": GasConfig(
        key="HCHO",
        name="Formaldehyde",
        collection_id="COPERNICUS/S5P/OFFL/L3_HCHO",
        band=(
            "tropospheric_HCHO_column"
            "_number_density"
        ),
        vis_min=0.0,
        vis_max=0.0005,
        display_unit="\u00b5mol/m\u00b2",
        display_scale=1e6,
    ),
}


def get_gas_config(key: str) -> GasConfig:
    """Look up a gas configuration by key."""
    try:
        return GAS_REGISTRY[key]
    except KeyError:
        valid = ", ".join(sorted(GAS_REGISTRY))
        raise ValueError(
            f"Unknown gas key {key!r}. "
            f"Valid keys: {valid}"
        ) from None
