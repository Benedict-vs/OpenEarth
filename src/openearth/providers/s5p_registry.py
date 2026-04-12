"""Central configuration registry for Sentinel-5P trace gases."""

from __future__ import annotations

from dataclasses import dataclass, field

# ------------------------------------------------------------------
# Per-gas palettes aligned with literature / Copernicus conventions
# ------------------------------------------------------------------

# NO2 – warm sequential (ESA TROPOMI / KNMI convention).
# Pale yellow → orange → red → dark maroon.
_PALETTE_NO2: list[str] = [
    "#ffffcc",
    "#ffeda0",
    "#fed976",
    "#feb24c",
    "#fd8d3c",
    "#fc4e2a",
    "#e31a1c",
    "#bd0026",
    "#800026",
    "#4d0013",
]

# SO2 – warm-to-violet sequential (volcanic plume convention).
_PALETTE_SO2: list[str] = [
    "#fff7f3",
    "#fde0dd",
    "#fcc5c0",
    "#fa9fb5",
    "#f768a1",
    "#dd3497",
    "#ae017e",
    "#7a0177",
    "#560060",
    "#340040",
]

# CO – black-body / thermal sequential (GEE catalog convention).
_PALETTE_CO: list[str] = [
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

# O3 – cool-to-warm sequential (NASA Ozone Watch convention).
_PALETTE_O3: list[str] = [
    "#1a0a5e",
    "#313695",
    "#4575b4",
    "#74add1",
    "#abd9e9",
    "#fee090",
    "#fdae61",
    "#f46d43",
    "#d73027",
    "#a50026",
]

# CH4 – viridis-like sequential (common in TROPOMI CH4 studies).
_PALETTE_CH4: list[str] = [
    "#440154",
    "#482878",
    "#3e4989",
    "#31688e",
    "#26828e",
    "#1f9e89",
    "#35b779",
    "#6ece58",
    "#b5de2b",
    "#fde725",
]

# HCHO – warm sequential with purple low-end.
_PALETTE_HCHO: list[str] = [
    "#f7f4f9",
    "#e7e1ef",
    "#d4b9da",
    "#c994c7",
    "#df65b0",
    "#e7298a",
    "#ce1256",
    "#980043",
    "#67001f",
    "#3d000f",
]

# Kept as a general-purpose fallback.
DEFAULT_PALETTE: list[str] = list(_PALETTE_NO2)


@dataclass(frozen=True)
class GasConfig:
    """Immutable descriptor for a single trace gas product."""

    key: str
    name: str
    collection_id: str
    band: str
    vis_min: float
    vis_max: float
    valid_min: float
    valid_max: float
    display_unit: str
    description: str = ""
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
        valid_min=-0.0001,
        valid_max=0.001,
        display_unit="\u00b5mol/m\u00b2",
        description=(
            "**Reading the NO\u2082 scale:** "
            "Tropospheric nitrogen dioxide column "
            "density from TROPOMI. "
            "**Low values** (pale) indicate clean "
            "air typical of rural or oceanic areas. "
            "**Moderate values** indicate background "
            "urban or industrial activity. "
            "**High values** (dark red) indicate "
            "intense combustion sources such as "
            "power plants, traffic corridors, or "
            "industrial zones. "
            "Useful for monitoring air quality and "
            "identifying pollution hotspots. "
            "**Safe limits (ground-level references "
            "— not directly comparable to satellite "
            "column values):** "
            "WHO guideline: 10 µg/m³ annual mean, "
            "25 µg/m³ 24-hour mean. "
            "Prolonged exposure above these levels "
            "increases respiratory and cardiovascular "
            "risk in humans and animals. "
            "Sustained high concentrations damage "
            "plant foliage and contribute to acid "
            "rain, harming terrestrial and aquatic "
            "ecosystems."
        ),
        display_scale=1e6,
        palette=_PALETTE_NO2,
    ),
    "SO2": GasConfig(
        key="SO2",
        name="Sulphur Dioxide",
        collection_id="COPERNICUS/S5P/OFFL/L3_SO2",
        band="SO2_column_number_density",
        vis_min=0.0,
        vis_max=0.0005,
        valid_min=-0.001,
        valid_max=0.01,
        display_unit="\u00b5mol/m\u00b2",
        description=(
            "**Reading the SO\u2082 scale:** "
            "Sulphur dioxide column density from "
            "TROPOMI. "
            "**Low values** (pale) indicate clean "
            "background conditions. "
            "**Elevated values** indicate volcanic "
            "degassing, coal-fired power plants, "
            "or smelters. "
            "**Very high values** (dark purple) "
            "indicate active volcanic eruptions "
            "or major industrial emissions. "
            "Useful for tracking volcanic plumes "
            "and industrial pollution sources. "
            "**Safe limits (ground-level references "
            "— not directly comparable to satellite "
            "column values):** "
            "WHO guideline: 40 µg/m³ 24-hour mean. "
            "Short-term exposure above this level "
            "aggravates asthma and respiratory "
            "conditions in humans and animals. "
            "Vegetation suffers leaf necrosis at "
            "concentrations above ~100 µg/m³. "
            "SO₂ also acidifies water bodies, "
            "harming fish and aquatic organisms."
        ),
        display_scale=1e6,
        palette=_PALETTE_SO2,
    ),
    "CO": GasConfig(
        key="CO",
        name="Carbon Monoxide",
        collection_id="COPERNICUS/S5P/OFFL/L3_CO",
        band="CO_column_number_density",
        vis_min=0.0,
        vis_max=0.05,
        valid_min=0.0,
        valid_max=0.1,
        display_unit="mmol/m\u00b2",
        description=(
            "**Reading the CO scale:** "
            "Carbon monoxide total column density "
            "from TROPOMI. "
            "**Low values** (dark) indicate clean "
            "background air. "
            "**Moderate values** indicate regional "
            "biomass burning or urban emissions. "
            "**High values** (bright yellow) "
            "indicate intense wildfires, "
            "agricultural burning, or major "
            "industrial emissions. "
            "Useful for tracking fire plumes and "
            "long-range pollution transport. "
            "**Safe limits (ground-level references "
            "— not directly comparable to satellite "
            "column values):** "
            "WHO guideline: 4 mg/m³ 24-hour mean. "
            "Exposure above ~35 ppm (1-hour) is "
            "dangerous to humans and animals, "
            "impairing oxygen transport in the blood. "
            "Plants are largely unaffected by ambient "
            "CO levels."
        ),
        display_scale=1e3,
        palette=_PALETTE_CO,
    ),
    "O3": GasConfig(
        key="O3",
        name="Ozone",
        collection_id="COPERNICUS/S5P/OFFL/L3_O3",
        band="O3_column_number_density",
        vis_min=0.07,
        vis_max=0.20,
        valid_min=0.0,
        valid_max=0.3,
        display_unit="mol/m\u00b2",
        description=(
            "**Reading the O\u2083 scale:** "
            "Total ozone column density from "
            "TROPOMI. "
            "**Low values** (blue/purple) indicate "
            "ozone-depleted regions, potentially "
            "near polar vortex boundaries. "
            "**Mid-range values** indicate typical "
            "background ozone levels. "
            "**High values** (red) indicate ozone "
            "accumulation from stratospheric "
            "intrusion or photochemical production. "
            "Useful for monitoring the ozone layer "
            "and tropospheric ozone episodes. "
            "**Safe limits (ground-level references "
            "— not directly comparable to satellite "
            "column values):** "
            "WHO guideline: 100 µg/m³ 8-hour mean "
            "(~50 ppb). Ground-level ozone above "
            "this threshold irritates airways in "
            "humans and animals. "
            "Crops and forests suffer reduced growth "
            "and visible leaf damage above ~60 ppb "
            "(AOT40 metric used in vegetation "
            "protection standards)."
        ),
        display_scale=1.0,
        palette=_PALETTE_O3,
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
        valid_min=1600.0,
        valid_max=2200.0,
        display_unit="ppb",
        description=(
            "**Reading the CH\u2084 scale:** "
            "Column-averaged dry-air methane mixing "
            "ratio from TROPOMI (~7 km resolution). "
            "**Values around 1850 ppb** represent "
            "the current global background. "
            "**Values above 1900 ppb** indicate "
            "regional enhancement from oil & gas "
            "infrastructure, wetlands, or livestock. "
            "**Values above 2000 ppb** indicate "
            "strong local sources or super-emitters. "
            "Useful for identifying large methane "
            "emission regions at coarse scale. "
            "**Safe limits:** "
            "Methane is not directly toxic at "
            "ambient atmospheric concentrations. "
            "Its primary concern is as a potent "
            "greenhouse gas (~80× CO₂ warming "
            "potential over 20 years). "
            "CH₄ becomes an explosion hazard only "
            "at 5–15 % by volume — far above the "
            "ppb levels observed by satellite. "
            "No specific WHO air-quality guideline "
            "exists for ambient methane."
        ),
        display_scale=1.0,
        palette=_PALETTE_CH4,
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
        valid_min=-0.0005,
        valid_max=0.005,
        display_unit="\u00b5mol/m\u00b2",
        description=(
            "**Reading the HCHO scale:** "
            "Tropospheric formaldehyde column "
            "density from TROPOMI. "
            "**Low values** (pale) indicate clean "
            "conditions with minimal VOC oxidation. "
            "**Moderate values** indicate biogenic "
            "emissions from forests (isoprene "
            "oxidation) or urban photochemistry. "
            "**High values** (dark red) indicate "
            "intense biomass burning, industrial "
            "VOC emissions, or petrochemical "
            "activity. "
            "Useful as a proxy for volatile organic "
            "compound emissions and fire detection. "
            "**Safe limits (ground-level references "
            "— not directly comparable to satellite "
            "column values):** "
            "WHO guideline: 100 µg/m³ for 30-minute "
            "indoor exposure. Formaldehyde is "
            "classified as carcinogenic to humans "
            "(IARC Group 1). Chronic exposure "
            "irritates eyes and airways in humans "
            "and animals. High concentrations can "
            "also damage plant cell membranes."
        ),
        display_scale=1e6,
        palette=_PALETTE_HCHO,
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
