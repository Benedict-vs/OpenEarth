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

# Green → yellow → orange → red for fire/burn indices.
FIRE_PALETTE: list[str] = [
    "#006837",
    "#1a9850",
    "#66bd63",
    "#a6d96a",
    "#d9ef8b",
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d73027",
    "#a50026",
]

# Brown → white → blue for snow indices.
SNOW_PALETTE: list[str] = [
    "#8c510a",
    "#bf812d",
    "#dfc27d",
    "#f6e8c3",
    "#f5f5f5",
    "#c7eae5",
    "#80cdc1",
    "#35978f",
    "#01665e",
    "#003c30",
]

# Green → yellow → brown for bare soil / tillage indices.
SOIL_PALETTE: list[str] = [
    "#006837",
    "#1a9850",
    "#66bd63",
    "#a6d96a",
    "#d9ef8b",
    "#fee08b",
    "#dfc27d",
    "#bf812d",
    "#8c510a",
    "#543005",
]

# Green → yellow → grey for built-up / urban indices.
URBAN_PALETTE: list[str] = [
    "#1a9850",
    "#66bd63",
    "#a6d96a",
    "#d9ef8b",
    "#ffffbf",
    "#d9d9d9",
    "#bdbdbd",
    "#969696",
    "#636363",
    "#252525",
]

# Blue → yellow → red for mineral / iron indices.
MINERAL_PALETTE: list[str] = [
    "#2166ac",
    "#4393c3",
    "#92c5de",
    "#d1e5f0",
    "#ffffbf",
    "#fee08b",
    "#fdae61",
    "#f46d43",
    "#d73027",
    "#a50026",
]

# Blue → green → brown for turbidity / sediment.
TURBIDITY_PALETTE: list[str] = [
    "#00008b",
    "#1e90ff",
    "#4682b4",
    "#87ceeb",
    "#b0e0e6",
    "#c8dbbe",
    "#a0a060",
    "#8b7d3c",
    "#6b4226",
    "#3d1c02",
]

# Blue → green → red for water quality / chlorophyll-a.
WATER_QUALITY_PALETTE: list[str] = [
    "#00008b",
    "#1e90ff",
    "#87ceeb",
    "#00cc66",
    "#66cc00",
    "#cccc00",
    "#ffaa00",
    "#ff6600",
    "#ff0000",
    "#990000",
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
    description: str = ""
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
        description=(
            "**Reading the NDVI scale:** "
            "NDVI measures vegetation greenness "
            "using (NIR \u2212 Red) / (NIR + Red). "
            "**Negative values** (red/brown) "
            "indicate water, bare soil, or built "
            "surfaces. "
            "**Values near 0** indicate sparse "
            "vegetation or dry ground. "
            "**Values 0.2\u20130.5** indicate shrubs, "
            "grass, or crops. "
            "**Values above 0.6** indicate dense, "
            "healthy vegetation such as forests."
        ),
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
        description=(
            "**Reading the NDWI scale:** "
            "NDWI highlights water using "
            "(Green \u2212 NIR) / (Green + NIR). "
            "**Positive values** (blue) indicate "
            "open water surfaces. "
            "**Values near 0** indicate moist soil "
            "or the water\u2013land boundary. "
            "**Negative values** (brown) indicate "
            "dry land, vegetation, or built "
            "surfaces."
        ),
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
        description=(
            "**Reading the EVI scale:** "
            "EVI is an enhanced vegetation index "
            "that corrects for atmospheric and "
            "soil background effects. "
            "**Negative values** indicate water "
            "or bare surfaces. "
            "**Values 0.1\u20130.3** indicate sparse "
            "vegetation or cropland. "
            "**Values 0.3\u20130.6** indicate moderate "
            "vegetation cover. "
            "**Values above 0.6** indicate dense "
            "tropical or temperate forests."
        ),
        palette=VEGETATION_PALETTE,
    ),
    # ── Priority 1 indices ─────────────────────────────────────
    "NDRE": S2IndexConfig(
        key="NDRE",
        name="Normalized Difference Red Edge",
        bands=["B8A", "B5"],
        expression="(B8A - B5) / (B8A + B5)",
        vis_min=-0.2,
        vis_max=0.8,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the NDRE scale:** "
            "NDRE uses the red-edge band (B5, "
            "705 nm) instead of visible red, making "
            "it more sensitive to chlorophyll "
            "variations in dense canopies where "
            "NDVI saturates. "
            "**Negative values** indicate bare soil "
            "or water. "
            "**Values 0.1\u20130.3** indicate moderate "
            "vegetation or stressed crops. "
            "**Values above 0.4** indicate healthy, "
            "dense vegetation with high chlorophyll "
            "content. "
            "Useful for precision agriculture and "
            "crop health monitoring."
        ),
        palette=VEGETATION_PALETTE,
    ),
    "CIre": S2IndexConfig(
        key="CIre",
        name="Chlorophyll Index Red Edge",
        bands=["B7", "B5"],
        expression="(B7 / B5) - 1",
        vis_min=0.0,
        vis_max=5.0,
        valid_min=0.0,
        valid_max=20.0,
        display_unit="index",
        description=(
            "**Reading the CIre scale:** "
            "The Chlorophyll Index Red Edge "
            "estimates leaf chlorophyll content "
            "using B7/B5 \u2212 1. "
            "**Values near 0** indicate very low "
            "chlorophyll (bare soil, senescent "
            "vegetation). "
            "**Values 1\u20133** indicate moderate "
            "chlorophyll typical of crops and "
            "grassland. "
            "**Values above 4** indicate very high "
            "chlorophyll in dense, healthy canopies. "
            "Useful for estimating crop nitrogen "
            "status and leaf chlorophyll content."
        ),
        palette=VEGETATION_PALETTE,
    ),
    "SAVI": S2IndexConfig(
        key="SAVI",
        name="Soil Adjusted Vegetation Index",
        bands=["B8", "B4"],
        expression=(
            "((B8 - B4) / (B8 + B4 + 0.5)) * 1.5"
        ),
        vis_min=-0.2,
        vis_max=0.8,
        valid_min=-1.5,
        valid_max=1.5,
        display_unit="index",
        description=(
            "**Reading the SAVI scale:** "
            "SAVI corrects NDVI for soil brightness "
            "in areas with sparse vegetation "
            "(L = 0.5). "
            "**Negative values** indicate water or "
            "very bright bare soil. "
            "**Values near 0** indicate bare ground "
            "with minimal vegetation. "
            "**Values 0.2\u20130.5** indicate sparse to "
            "moderate vegetation cover. "
            "**Values above 0.5** indicate dense "
            "vegetation. "
            "Useful in arid and semi-arid regions "
            "where soil background strongly "
            "influences NDVI."
        ),
        palette=VEGETATION_PALETTE,
    ),
    "LAI": S2IndexConfig(
        key="LAI",
        name="Leaf Area Index (CIre-based)",
        bands=["B7", "B5"],
        expression=(
            "3.618 * ((B7 / B5) - 1) - 0.118"
        ),
        vis_min=0.0,
        vis_max=6.0,
        valid_min=0.0,
        valid_max=8.0,
        display_unit="m\u00b2/m\u00b2",
        description=(
            "**Reading the LAI scale:** "
            "Leaf Area Index estimates the total "
            "one-sided leaf area per unit ground "
            "area, derived from the Chlorophyll "
            "Index Red Edge. "
            "**Values 0\u20131** indicate bare or very "
            "sparse vegetation. "
            "**Values 2\u20134** indicate cropland or "
            "grassland with moderate cover. "
            "**Values above 5** indicate dense "
            "forests or fully closed canopies. "
            "Useful for crop growth monitoring, "
            "yield estimation, and ecosystem "
            "productivity assessment."
        ),
        palette=VEGETATION_PALETTE,
    ),
    "MNDWI": S2IndexConfig(
        key="MNDWI",
        name="Modified NDWI (open water)",
        bands=["B3", "B11"],
        expression="(B3 - B11) / (B3 + B11)",
        vis_min=-0.8,
        vis_max=0.8,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the MNDWI scale:** "
            "MNDWI uses Green and SWIR-1 bands to "
            "detect open water while suppressing "
            "built-up area noise that affects NDWI. "
            "**Positive values** (blue) indicate "
            "open water bodies. "
            "**Values near 0** indicate the "
            "water\u2013land transition or moist soil. "
            "**Negative values** indicate dry land, "
            "vegetation, or built-up surfaces. "
            "Useful for mapping water extent, flood "
            "delineation, and separating water from "
            "urban areas."
        ),
        palette=WATER_PALETTE,
    ),
    "NBR": S2IndexConfig(
        key="NBR",
        name="Normalized Burn Ratio",
        bands=["B8A", "B12"],
        expression="(B8A - B12) / (B8A + B12)",
        vis_min=-0.5,
        vis_max=1.0,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the NBR scale:** "
            "NBR highlights burned areas using "
            "(NIR \u2212 SWIR2) / (NIR + SWIR2). "
            "**High positive values** indicate "
            "healthy vegetation with strong NIR "
            "reflectance. "
            "**Values near 0** indicate bare soil "
            "or sparse vegetation. "
            "**Negative values** indicate recently "
            "burned areas where SWIR reflectance "
            "exceeds NIR due to charred material. "
            "Useful for fire scar mapping and burn "
            "severity assessment."
        ),
        palette=FIRE_PALETTE,
    ),
    "BAI": S2IndexConfig(
        key="BAI",
        name="Burned Area Index",
        bands=["B4", "B8"],
        expression=(
            "1.0 / ((0.1 - B4) * (0.1 - B4) "
            "+ (0.06 - B8) * (0.06 - B8))"
        ),
        vis_min=0.0,
        vis_max=50.0,
        valid_min=0.0,
        valid_max=100.0,
        display_unit="index",
        description=(
            "**Reading the BAI scale:** "
            "BAI measures spectral distance to a "
            "charcoal reference point in Red\u2013NIR "
            "space. "
            "**Low values** (near 0) indicate "
            "healthy vegetation or bare soil far "
            "from the char spectral signature. "
            "**Values 10\u201330** indicate moderately "
            "burned or fire-affected areas. "
            "**High values** (above 40) indicate "
            "recently and severely burned surfaces. "
            "Useful for identifying active fire "
            "scars and post-fire damage assessment."
        ),
        palette=FIRE_PALETTE,
    ),
    "NDSI": S2IndexConfig(
        key="NDSI",
        name="Normalized Difference Snow Index",
        bands=["B3", "B11"],
        expression="(B3 - B11) / (B3 + B11)",
        vis_min=-0.5,
        vis_max=1.0,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the NDSI scale:** "
            "NDSI detects snow and ice using "
            "(Green \u2212 SWIR) / (Green + SWIR). "
            "Snow is highly reflective in visible "
            "bands but absorbs SWIR. "
            "**Values above 0.4** reliably indicate "
            "snow or ice cover. "
            "**Values 0\u20130.4** indicate partial "
            "snow cover, thin snow, or wet soil. "
            "**Negative values** indicate bare "
            "ground, vegetation, or water. "
            "Useful for snow cover mapping, "
            "snowmelt monitoring, and glacier "
            "extent tracking."
        ),
        palette=SNOW_PALETTE,
    ),
    # ── Priority 2 indices ─────────────────────────────────────
    "BSI": S2IndexConfig(
        key="BSI",
        name="Bare Soil Index",
        bands=["B11", "B4", "B8", "B2"],
        expression=(
            "((B11 + B4) - (B8 + B2)) "
            "/ ((B11 + B4) + (B8 + B2))"
        ),
        vis_min=-0.5,
        vis_max=0.5,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the BSI scale:** "
            "BSI highlights bare soil by combining "
            "SWIR + Red versus NIR + Blue. "
            "**High positive values** indicate "
            "exposed bare soil, rock, or "
            "construction sites. "
            "**Values near 0** indicate mixed "
            "land cover. "
            "**Negative values** indicate dense "
            "vegetation or water. "
            "Useful for mapping soil exposure, "
            "urban expansion, and desertification "
            "monitoring."
        ),
        palette=SOIL_PALETTE,
    ),
    "NDTI": S2IndexConfig(
        key="NDTI",
        name="Normalized Difference Tillage Index",
        bands=["B11", "B12"],
        expression="(B11 - B12) / (B11 + B12)",
        vis_min=-0.3,
        vis_max=0.3,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the NDTI scale:** "
            "NDTI uses two SWIR bands to detect "
            "crop residue on the soil surface. "
            "**Higher values** indicate more crop "
            "residue cover (conservation tillage). "
            "**Values near 0** indicate bare soil "
            "with minimal residue. "
            "**Lower values** indicate exposed "
            "mineral soil. "
            "Useful for monitoring tillage "
            "practices and crop residue management."
        ),
        palette=SOIL_PALETTE,
    ),
    "NDMI": S2IndexConfig(
        key="NDMI",
        name="Normalized Difference Moisture Index",
        bands=["B8A", "B11"],
        expression="(B8A - B11) / (B8A + B11)",
        vis_min=-0.5,
        vis_max=0.7,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the NDMI scale:** "
            "NDMI measures canopy water content "
            "using (NIR \u2212 SWIR1) / (NIR + SWIR1). "
            "**High values** indicate high leaf "
            "water content in healthy vegetation. "
            "**Values near 0** indicate moderate "
            "moisture stress or dry vegetation. "
            "**Negative values** indicate very dry "
            "vegetation, bare soil, or built "
            "surfaces. "
            "Useful for drought monitoring, fire "
            "risk assessment, and irrigation "
            "planning."
        ),
        palette=WATER_PALETTE,
    ),
    "NDBI": S2IndexConfig(
        key="NDBI",
        name="Normalized Difference Built-up Index",
        bands=["B11", "B8"],
        expression="(B11 - B8) / (B11 + B8)",
        vis_min=-0.5,
        vis_max=0.5,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="index",
        description=(
            "**Reading the NDBI scale:** "
            "NDBI highlights built-up and impervious "
            "surfaces using (SWIR1 \u2212 NIR) / "
            "(SWIR1 + NIR). "
            "**Positive values** indicate built-up "
            "areas, roads, and bare rock where SWIR "
            "exceeds NIR reflectance. "
            "**Values near 0** indicate mixed "
            "land cover. "
            "**Negative values** indicate dense "
            "vegetation where NIR dominates. "
            "Useful for urban extent mapping and "
            "impervious surface detection."
        ),
        palette=URBAN_PALETTE,
    ),
    "CLAY": S2IndexConfig(
        key="CLAY",
        name="Clay Mineral Index",
        bands=["B11", "B12"],
        expression="B11 / B12",
        vis_min=0.5,
        vis_max=2.5,
        valid_min=0.0,
        valid_max=5.0,
        display_unit="ratio",
        description=(
            "**Reading the Clay Mineral Index:** "
            "The ratio B11/B12 exploits absorption "
            "features of clay minerals near 2200 nm. "
            "**High values** indicate clay-rich "
            "soils or alteration zones where B11 is "
            "much brighter than B12. "
            "**Values near 1** indicate neutral "
            "surfaces with similar SWIR reflectance. "
            "**Low values** indicate non-clay "
            "minerals or organic soils. "
            "Useful for geological mapping, soil "
            "mineralogy, and hydrothermal alteration "
            "zone identification."
        ),
        palette=SOIL_PALETTE,
    ),
    "IRON": S2IndexConfig(
        key="IRON",
        name="Iron Oxide Index",
        bands=["B4", "B2"],
        expression="B4 / B2",
        vis_min=0.5,
        vis_max=3.0,
        valid_min=0.0,
        valid_max=5.0,
        display_unit="ratio",
        description=(
            "**Reading the Iron Oxide Index:** "
            "The ratio B4/B2 (Red/Blue) exploits "
            "the strong absorption of iron oxides "
            "in blue wavelengths. "
            "**High values** indicate iron-rich "
            "soils, laterite, or oxidized rock "
            "surfaces. "
            "**Values near 1** indicate neutral "
            "surfaces. "
            "**Low values** indicate non-ferrous "
            "materials or vegetated areas. "
            "Useful for geological exploration, "
            "mineral mapping, and soil composition "
            "analysis."
        ),
        palette=MINERAL_PALETTE,
    ),
    "TURB": S2IndexConfig(
        key="TURB",
        name="Turbidity / TSM",
        bands=["B4", "B3"],
        expression=(
            "1246.0 * ((B4 - B3) / (B4 + B3)) "
            "+ 340.0"
        ),
        vis_min=0.0,
        vis_max=500.0,
        valid_min=0.0,
        valid_max=500.0,
        display_unit="mg/L",
        description=(
            "**Reading the Turbidity scale:** "
            "Estimates total suspended matter (TSM) "
            "in water using a calibrated Red\u2013Green "
            "ratio. "
            "**Low values** (blue) indicate clear "
            "water with little suspended sediment. "
            "**Values 50\u2013200 mg/L** indicate "
            "moderately turbid water typical of "
            "rivers or coastal zones. "
            "**High values** (brown) indicate very "
            "turbid water with heavy sediment load. "
            "Useful for monitoring water quality, "
            "sediment transport, and dredging "
            "impacts."
        ),
        palette=TURBIDITY_PALETTE,
    ),
    "CHLA": S2IndexConfig(
        key="CHLA",
        name="Chlorophyll-a (inland water)",
        bands=["B5", "B4"],
        expression=None,
        vis_min=0.0,
        vis_max=50.0,
        valid_min=0.0,
        valid_max=100.0,
        display_unit="\u00b5g/L",
        description=(
            "**Reading the Chlorophyll-a scale:** "
            "Estimates chlorophyll-a concentration "
            "in inland waters using 4.26 \u00d7 "
            "(B5/B4)\u00b3\u00b7\u2079\u2074. "
            "**Low values** (blue) indicate "
            "oligotrophic (clear) water with little "
            "algal biomass. "
            "**Values 10\u201330 \u00b5g/L** indicate "
            "mesotrophic conditions with moderate "
            "algal growth. "
            "**High values** (red) indicate "
            "eutrophic water with algal blooms. "
            "Useful for monitoring lake and "
            "reservoir water quality and detecting "
            "harmful algal blooms."
        ),
        palette=WATER_QUALITY_PALETTE,
    ),
    # ── Methane-only proxies ───────────────────────────────────
    "MBSP": S2IndexConfig(
        key="MBSP",
        name="Multi-Band Single-Pass CH\u2084 proxy",
        bands=["B12", "B11"],
        expression="(B12 - B11) / B11",
        vis_min=-0.5,
        vis_max=0.1,
        valid_min=-1.0,
        valid_max=1.0,
        display_unit="ratio",
        description=(
            "**Reading the MBSP scale:** "
            "The Multi-Band Single-Pass (MBSP) index "
            "highlights methane by computing "
            "(B12 \u2212 B11) / B11, a normalized "
            "SWIR difference. "
            "**More negative values** indicate "
            "stronger absorption in B12 relative to "
            "B11 \u2014 consistent with methane "
            "absorbing in the 2190 nm SWIR2 band. "
            "**Values near zero** suggest no "
            "differential absorption (no plume). "
            "**Positive values** indicate B12 is "
            "brighter than B11 (typical of bare "
            "soil or mineral surfaces). "
            "Look for localized dark patches "
            "(negative values) against a uniform "
            "background."
        ),
        palette=METHANE_PALETTE,
        methane_only=True,
    ),
    "B12_B11": S2IndexConfig(
        key="B12_B11",
        name="SWIR Band Ratio (B12/B11, CH\u2084 proxy)",
        bands=["B12", "B11"],
        expression="B12 / B11",
        vis_min=0.3,
        vis_max=0.9,
        valid_min=0.0,
        valid_max=2.0,
        display_unit="ratio",
        description=(
            "**Reading the B12/B11 ratio scale:** "
            "This shows the ratio of SWIR2 (B12, "
            "2190 nm) to SWIR1 (B11, 1610 nm) "
            "reflectance. "
            "**Lower ratio values** indicate that "
            "B12 is darker relative to B11 \u2014 "
            "consistent with methane absorption "
            "reducing the B12 signal. "
            "**Values near 1.0** indicate similar "
            "reflectance in both bands (no "
            "differential absorption). "
            "**Values above 1.0** indicate B12 is "
            "brighter than B11. "
            "Methane plumes appear as localized "
            "dips in the ratio compared to the "
            "surrounding area."
        ),
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
        description=(
            "**Reading the CH\u2084 anomaly scale:** "
            "Values show the change in the B12/B11 "
            "reflectance ratio relative to the "
            "baseline period mean. "
            "**Negative values** (blue) indicate "
            "stronger SWIR absorption at the target "
            "date \u2014 consistent with a methane "
            "plume absorbing in Band 12. "
            "**Values near zero** (white/yellow) "
            "indicate no change from the baseline. "
            "**Positive values** (red) indicate "
            "higher B12/B11 ratio than the baseline "
            "(surface change, not methane). "
            "Typical methane plumes appear as "
            "localized negative anomalies in the "
            "range \u22120.01 to \u22120.05."
        ),
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
