"""Constants and sidebar configuration for OpenEarth Explorer."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta

import streamlit as st

from openearth.providers.s1_registry import S1_REGISTRY
from openearth.providers.s2_registry import S2_REGISTRY
from openearth.providers.s5p_registry import GAS_REGISTRY


# ── Constants ──────────────────────────────────────────────────

ROI_EXAMPLES: dict[str, tuple[float, float, float, float]] = {
    # Continents
    "Europe": (-25.0, 34.0, 45.0, 72.0),
    "North America": (-170.0, 15.0, -50.0, 72.0),
    "South America": (-82.0, -56.0, -34.0, 13.0),
    "Africa": (-18.0, -35.0, 52.0, 37.0),
    "Asia": (25.0, -10.0, 180.0, 75.0),
    "Oceania": (110.0, -50.0, 180.0, 0.0),
    "Antarctica": (-180.0, -90.0, 180.0, -60.0),
    "Entire Earth": (-180.0, -90.0, 180.0, 90.0),
    # Cities
    "Heidelberg (Germany)": (8.58, 49.35, 8.77, 49.46),
    "London (UK)": (-0.51, 51.28, 0.33, 51.70),
    "Berlin (Germany)": (
        13.09, 52.33, 13.76, 52.68,
    ),
    "New York (USA)": (
        -74.26, 40.49, -73.69, 40.92,
    ),
    "Merida (Mexico)": (
        -89.80, 20.85, -89.50, 21.10,
    ),
    "Barranquilla (Colombia)": (
        -74.93, 10.90, -74.70, 11.10,
    ),
    # Methane emission sites
    "CH4: Korpezhe, Turkmenistan": (
        53.7, 38.2, 54.7, 38.8,
    ),
    "CH4: Galkynysh, Turkmenistan": (
        61.8, 36.9, 62.9, 37.7,
    ),
    "CH4: Permian Basin (USA)": (
        -104.5, 31.0, -103.0, 32.5,
    ),
    "CH4: Hassi Messaoud, Algeria": (
        5.4, 31.2, 6.4, 32.0,
    ),
    "CH4: Basra oil fields, Iraq": (
        46.9, 30.0, 47.8, 31.0,
    ),
    "CH4: Four Corners (USA)": (
        -109.6, 36.5, -108.5, 37.5,
    ),
    "CH4: Upper Silesia, Poland": (
        18.5, 50.0, 19.5, 50.5,
    ),
}
DEFAULT_EXAMPLE = "Entire Earth"

# Suggested date ranges for methane sites.
# Maps ROI example name → (start, end) ISO strings.
# These are loaded when the user picks a CH4 example.
CH4_DATE_HINTS: dict[str, tuple[str, str]] = {
    "CH4: Korpezhe, Turkmenistan": (
        "2024-06-01", "2024-12-01",
    ),
    "CH4: Galkynysh, Turkmenistan": (
        "2024-06-01", "2024-12-01",
    ),
    "CH4: Permian Basin (USA)": (
        "2024-03-01", "2024-09-01",
    ),
    "CH4: Hassi Messaoud, Algeria": (
        "2024-04-01", "2024-10-01",
    ),
    "CH4: Basra oil fields, Iraq": (
        "2024-05-01", "2024-11-01",
    ),
    "CH4: Four Corners (USA)": (
        "2024-03-01", "2024-09-01",
    ),
    "CH4: Upper Silesia, Poland": (
        "2024-04-01", "2024-10-01",
    ),
}

_SOURCE_LABELS = {
    "Sentinel-5P (Trace Gases)": "s5p",
    "Sentinel-2 (Spectral Indices)": "s2",
    "Sentinel-1 (SAR)": "s1",
}

TRACE_GASES: dict[str, str] = {
    k: cfg.name for k, cfg in GAS_REGISTRY.items()
}
S2_INDICES: dict[str, str] = {
    k: cfg.name
    for k, cfg in S2_REGISTRY.items()
    if not cfg.methane_only
}
S1_VARIABLES: dict[str, str] = {
    k: cfg.name for k, cfg in S1_REGISTRY.items()
}


# ── Sidebar output ────────────────────────────────────────────

@dataclass
class SidebarConfig:
    project_id: str
    authenticate_on_fail: bool
    source: str
    selected_keys: list[str]
    west: float
    south: float
    east: float
    north: float
    start_date: date
    end_date_inclusive: date
    run: bool
    mode: str = "explorer"
    methane_s2_layers: list[str] = field(
        default_factory=lambda: ["MBSP"],
    )
    methane_show_s5p: bool = True
    methane_show_rgb: bool = False
    methane_mask_vegetation: bool = True
    methane_mask_water: bool = True
    methane_ndvi_threshold: float = 0.3
    methane_ndwi_threshold: float = 0.0
    methane_show_s1: bool = False
    methane_s1_variable: str = "VV"
    show_wind: bool = False
    methane_show_classification: bool = False
    methane_cls_s1_high: float = -10.0
    methane_cls_ndvi_veg: float = 0.35
    methane_cls_ndwi_water: float = 0.1
    methane_cls_methane_thresh: float = -0.02
    methane_cls_thermal_b11: float = 0.5
    methane_cls_thermal_b12: float = 0.5
    methane_cls_geo_methane: float = -0.04
    methane_cls_ndvi_barren: float = 0.1


_METHANE_LAYER_OPTIONS: dict[str, str] = {
    "MBSP": "MBSP — (B12−B11)/B11",
    "B12_B11": "B12/B11 ratio",
    "CH4_ANOMALY": "CH₄ anomaly (B12/B11 vs baseline)",
}


def _render_methane_sidebar() -> dict:
    """Render methane-specific sidebar controls.

    Returns a dict with all methane sidebar settings.
    """
    # ── Methane proxies ──────────────────────────────
    st.sidebar.header("Methane Proxies")
    s2_layers = st.sidebar.multiselect(
        "S2 proxy layers",
        options=list(_METHANE_LAYER_OPTIONS.keys()),
        default=["MBSP"],
        format_func=lambda k: _METHANE_LAYER_OPTIONS[k],
        key="methane_s2_layers",
    )

    # ── Overlay layers ───────────────────────────────
    st.sidebar.header("Overlay Layers")
    show_s5p = st.sidebar.checkbox(
        "S5P CH\u2084 (coarse, ~7 km)",
        value=True,
        key="methane_show_s5p",
    )
    show_s1 = st.sidebar.checkbox(
        "S1 SAR context",
        value=False,
        help=(
            "Overlay Sentinel-1 SAR backscatter to "
            "identify infrastructure, wetlands, and "
            "surface features."
        ),
        key="methane_show_s1",
    )
    s1_variable = "VV"
    if show_s1:
        s1_variable = st.sidebar.radio(
            "SAR variable",
            options=["VV", "VH", "VV_VH_RATIO"],
            format_func=lambda k: {
                "VV": "VV (co-pol, infrastructure)",
                "VH": "VH (cross-pol, vegetation)",
                "VV_VH_RATIO": "VV/VH ratio (land cover)",
            }[k],
            key="methane_s1_var",
        )
    show_wind = st.sidebar.checkbox(
        "ERA5 wind overlay",
        value=False,
        help=(
            "Overlay wind direction and speed arrows "
            "from ERA5 reanalysis to help trace "
            "methane plume origins."
        ),
        key="methane_show_wind",
    )
    show_classification = st.sidebar.checkbox(
        "Source classification",
        value=False,
        help=(
            "Auto-classify methane emission sources "
            "using S1 SAR, NDVI, NDWI, and MBSP."
        ),
        key="methane_show_classification",
    )
    cls_s1_high = -10.0
    cls_ndvi_veg = 0.35
    cls_ndwi_water = 0.1
    cls_methane_thresh = -0.02
    cls_thermal_b11 = 0.5
    cls_thermal_b12 = 0.5
    cls_geo_methane = -0.04
    cls_ndvi_barren = 0.1
    if show_classification:
        with st.sidebar.expander("Classification thresholds"):
            cls_s1_high = st.slider(
                "S1 VV high (dB)",
                -20.0, 0.0, -10.0,
                key="cls_s1_high",
            )
            cls_ndvi_veg = st.slider(
                "NDVI vegetation",
                0.0, 1.0, 0.35,
                key="cls_ndvi",
            )
            cls_ndwi_water = st.slider(
                "NDWI water",
                -0.5, 0.5, 0.1,
                key="cls_ndwi",
            )
            cls_methane_thresh = st.slider(
                "MBSP methane",
                -0.1, 0.0, -0.02,
                key="cls_methane",
            )
            cls_thermal_b11 = st.slider(
                "Thermal B11",
                0.0, 1.0, 0.5,
                help=(
                    "B11 reflectance above this indicates "
                    "thermal emission (gas flares)."
                ),
                key="cls_thermal_b11",
            )
            cls_thermal_b12 = st.slider(
                "Thermal B12",
                0.0, 1.0, 0.5,
                help=(
                    "B12 reflectance above this indicates "
                    "thermal emission (gas flares)."
                ),
                key="cls_thermal_b12",
            )
            cls_geo_methane = st.slider(
                "Geo seep MBSP",
                -0.1, 0.0, -0.04,
                help=(
                    "Stricter MBSP threshold for "
                    "geological seep classification."
                ),
                key="cls_geo_methane",
            )
            cls_ndvi_barren = st.slider(
                "Barren NDVI cutoff",
                0.0, 0.3, 0.1,
                help=(
                    "NDVI below this is barren desert "
                    "(suppresses geological seep)."
                ),
                key="cls_ndvi_barren",
            )
    show_rgb = st.sidebar.checkbox(
        "RGB composite (true colour)",
        value=False,
        help=(
            "Overlay a true-colour image to help "
            "distinguish surface features from "
            "methane signals."
        ),
        key="methane_show_rgb",
    )

    # ── Masking ──────────────────────────────────────
    st.sidebar.header("Masking")
    mask_veg = st.sidebar.checkbox(
        "Mask vegetation (NDVI)",
        value=True,
        key="methane_mask_veg",
    )
    mask_water = st.sidebar.checkbox(
        "Mask water (NDWI)",
        value=True,
        key="methane_mask_water",
    )

    ndvi_thresh = 0.3
    ndwi_thresh = 0.0
    with st.sidebar.expander("Mask thresholds"):
        ndvi_thresh = st.slider(
            "NDVI threshold",
            0.0, 1.0, 0.3,
            key="methane_ndvi_thresh",
        )
        ndwi_thresh = st.slider(
            "NDWI threshold",
            -0.5, 0.5, 0.0,
            key="methane_ndwi_thresh",
        )

    return {
        "s2_layers": s2_layers,
        "show_s5p": show_s5p,
        "show_rgb": show_rgb,
        "mask_veg": mask_veg,
        "mask_water": mask_water,
        "ndvi_thresh": ndvi_thresh,
        "ndwi_thresh": ndwi_thresh,
        "show_s1": show_s1,
        "s1_variable": s1_variable,
        "show_wind": show_wind,
        "show_classification": show_classification,
        "cls_s1_high": cls_s1_high,
        "cls_ndvi_veg": cls_ndvi_veg,
        "cls_ndwi_water": cls_ndwi_water,
        "cls_methane_thresh": cls_methane_thresh,
        "cls_thermal_b11": cls_thermal_b11,
        "cls_thermal_b12": cls_thermal_b12,
        "cls_geo_methane": cls_geo_methane,
        "cls_ndvi_barren": cls_ndvi_barren,
    }


def render_sidebar() -> SidebarConfig:
    """Render the sidebar and return all user inputs."""
    from app.roi import set_bbox

    st.sidebar.header("Configuration")
    project_default = os.getenv(
        "OPENEARTH_EE_PROJECT", "openearth-488015",
    )
    project_id = st.sidebar.text_input(
        "Earth Engine project ID",
        value=project_default,
    )
    authenticate_on_fail = st.sidebar.checkbox(
        "Authenticate on initialization failure",
        value=True,
    )

    # ── Mode toggle ───────────────────────────────────────
    st.sidebar.header("Mode")
    mode_label = st.sidebar.radio(
        "Application mode",
        options=["Explorer", "Methane Detection"],
        index=0,
        horizontal=True,
        key="app_mode_radio",
    )
    is_methane = mode_label == "Methane Detection"

    # ── Methane-specific or Explorer-specific controls ─────
    methane_s2_layers: list[str] = ["MBSP"]
    methane_show_s5p = True
    methane_show_rgb = False
    methane_mask_veg = True
    methane_mask_water = True
    methane_ndvi_thresh = 0.3
    methane_ndwi_thresh = 0.0
    methane_show_s1 = False
    methane_s1_variable = "VV"
    show_wind = False
    methane_show_classification = False
    methane_cls_s1_high = -10.0
    methane_cls_ndvi_veg = 0.35
    methane_cls_ndwi_water = 0.1
    methane_cls_methane_thresh = -0.02
    methane_cls_thermal_b11 = 0.5
    methane_cls_thermal_b12 = 0.5
    methane_cls_geo_methane = -0.04
    methane_cls_ndvi_barren = 0.1

    if is_methane:
        meth = _render_methane_sidebar()

        methane_s2_layers = meth["s2_layers"]
        methane_show_s5p = meth["show_s5p"]
        methane_show_rgb = meth["show_rgb"]
        methane_mask_veg = meth["mask_veg"]
        methane_mask_water = meth["mask_water"]
        methane_ndvi_thresh = meth["ndvi_thresh"]
        methane_ndwi_thresh = meth["ndwi_thresh"]
        methane_show_s1 = meth["show_s1"]
        methane_s1_variable = meth["s1_variable"]
        show_wind = meth["show_wind"]
        methane_show_classification = meth["show_classification"]
        methane_cls_s1_high = meth["cls_s1_high"]
        methane_cls_ndvi_veg = meth["cls_ndvi_veg"]
        methane_cls_ndwi_water = meth["cls_ndwi_water"]
        methane_cls_methane_thresh = meth["cls_methane_thresh"]
        methane_cls_thermal_b11 = meth["cls_thermal_b11"]
        methane_cls_thermal_b12 = meth["cls_thermal_b12"]
        methane_cls_geo_methane = meth["cls_geo_methane"]
        methane_cls_ndvi_barren = meth["cls_ndvi_barren"]

        source = "methane"
        selected_keys = (
            (["CH4"] if methane_show_s5p else [])
            + methane_s2_layers
        )
        if not selected_keys:
            st.sidebar.warning(
                "Enable at least one methane layer.",
            )
            selected_keys = ["MBSP"]
    else:
        st.sidebar.header("Data Source")
        source_label = st.sidebar.radio(
            "Satellite",
            options=list(_SOURCE_LABELS.keys()),
            index=0,
            key="source_radio",
        )
        source = _SOURCE_LABELS[source_label]

        if source == "s2":
            variables = S2_INDICES
        elif source == "s1":
            variables = S1_VARIABLES
        else:
            variables = TRACE_GASES

        selected_keys = st.sidebar.multiselect(
            "Variables",
            options=list(variables.keys()),
            format_func=lambda k: (
                f"{k} \u2013 {variables[k]}"
            ),
            key="variable_select",
        )
        if not selected_keys:
            st.sidebar.warning(
                "Select at least one variable.",
            )
            selected_keys = [list(variables.keys())[0]]

        if source == "s2":
            st.sidebar.info(
                "Clouds are automatically masked using the "
                "S2 Cloud Probability dataset (s2cloudless)."
            )

    st.sidebar.header("ROI (Region of Interest)")

    # Quick-start: load a predefined region
    st.sidebar.caption("Quick start:")
    if is_methane:
        # Show CH4 sites first in methane mode.
        roi_options = sorted(
            ROI_EXAMPLES.keys(),
            key=lambda k: (0 if k.startswith("CH4:") else 1, k),
        )
        default_idx = 0
    else:
        roi_options = list(ROI_EXAMPLES.keys())
        default_idx = roi_options.index(DEFAULT_EXAMPLE)
    selected_example = st.sidebar.selectbox(
        "Example regions",
        options=roi_options,
        index=default_idx,
    )
    if st.sidebar.button("Load example ROI"):
        set_bbox(*ROI_EXAMPLES[selected_example])
        hint = CH4_DATE_HINTS.get(selected_example)
        if hint:
            st.session_state["date_start"] = (
                date.fromisoformat(hint[0])
            )
            st.session_state["date_end"] = (
                date.fromisoformat(hint[1])
            )
        st.rerun()

    # Manual coordinate inputs in 2×2 grid
    roi_row1 = st.sidebar.columns(2)
    with roi_row1[0]:
        st.number_input(
            "West (lon)", key="roi_west",
            format="%.4f",
            help="Longitude: -180 to 180",
        )
    with roi_row1[1]:
        st.number_input(
            "East (lon)", key="roi_east",
            format="%.4f",
            help="Longitude: -180 to 180",
        )
    roi_row2 = st.sidebar.columns(2)
    with roi_row2[0]:
        st.number_input(
            "South (lat)", key="roi_south",
            format="%.4f",
            help="Latitude: -90 to 90",
        )
    with roi_row2[1]:
        st.number_input(
            "North (lat)", key="roi_north",
            format="%.4f",
            help="Latitude: -90 to 90",
        )

    # "Use drawn ROI" — reads from the draw-map widget
    drawn_bbox = st.session_state.get("drawn_bbox")
    if drawn_bbox is not None:
        if st.sidebar.button("Use drawn ROI"):
            st.session_state["pending_bbox"] = drawn_bbox
            st.session_state.pop("drawn_bbox", None)
            st.rerun()
    else:
        st.sidebar.caption(
            "Or draw a region on the map.",
        )

    west = float(st.session_state["roi_west"])
    south = float(st.session_state["roi_south"])
    east = float(st.session_state["roi_east"])
    north = float(st.session_state["roi_north"])

    st.sidebar.header("Time Range")
    # S2 and S1 are heavier per query (high resolution) so default to
    # 90 days; S5P covers the full atmosphere so 365 days is fine.
    # Methane mode uses S2 proxies → 90 days.
    default_days = (
        90 if source == "methane" else 365
    )

    # OFFL processing latency per source
    default_end_offset = (
        5 if source == "s5p"
        else 2 if source == "s1"
        else 3  # s2, methane
    )
    default_end = date.today() - timedelta(days=default_end_offset)

    default_start = default_end - timedelta(
        days=default_days,
    )

    prev_source = st.session_state.get("_prev_source")
    if prev_source != source or "date_start" not in st.session_state:
        st.session_state["date_start"] = default_start
        st.session_state["date_end"] = default_end
        st.session_state["_prev_source"] = source

    start_date = st.sidebar.date_input(
        "Start date",
        key="date_start",
    )
    end_date_inclusive = st.sidebar.date_input(
        "End date (inclusive)",
        key="date_end",
    )

    if end_date_inclusive > date.today():
        st.sidebar.info(
            "End date is in the future \u2014 satellite data "
            "may not yet be available for recent dates."
        )

    run = st.sidebar.button(
        "Load Map", type="primary",
    )

    return SidebarConfig(
        project_id=project_id,
        authenticate_on_fail=authenticate_on_fail,
        source=source,
        selected_keys=selected_keys,
        west=west,
        south=south,
        east=east,
        north=north,
        start_date=start_date,
        end_date_inclusive=end_date_inclusive,
        run=run,
        mode="methane" if is_methane else "explorer",
        methane_s2_layers=methane_s2_layers,
        methane_show_s5p=methane_show_s5p,
        methane_show_rgb=methane_show_rgb,
        methane_mask_vegetation=methane_mask_veg,
        methane_mask_water=methane_mask_water,
        methane_ndvi_threshold=methane_ndvi_thresh,
        methane_ndwi_threshold=methane_ndwi_thresh,
        methane_show_s1=methane_show_s1,
        methane_s1_variable=methane_s1_variable,
        show_wind=show_wind,
        methane_show_classification=methane_show_classification,
        methane_cls_s1_high=methane_cls_s1_high,
        methane_cls_ndvi_veg=methane_cls_ndvi_veg,
        methane_cls_ndwi_water=methane_cls_ndwi_water,
        methane_cls_methane_thresh=methane_cls_methane_thresh,
        methane_cls_thermal_b11=methane_cls_thermal_b11,
        methane_cls_thermal_b12=methane_cls_thermal_b12,
        methane_cls_geo_methane=methane_cls_geo_methane,
        methane_cls_ndvi_barren=methane_cls_ndvi_barren,
    )
