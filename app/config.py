"""Constants and sidebar configuration for OpenEarth Explorer."""

from __future__ import annotations

import os
from dataclasses import dataclass
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
    k: cfg.name for k, cfg in S2_REGISTRY.items()
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
        st.sidebar.warning("Select at least one variable.")
        selected_keys = [list(variables.keys())[0]]

    st.sidebar.header("ROI (Region of Interest)")

    # Quick-start: load a predefined region
    st.sidebar.caption("Quick start:")
    selected_example = st.sidebar.selectbox(
        "Example regions",
        options=list(ROI_EXAMPLES.keys()),
        index=list(ROI_EXAMPLES.keys()).index(
            DEFAULT_EXAMPLE,
        ),
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
    default_days = 90 if source in ("s2", "s1") else 365
    default_start = date.today() - timedelta(
        days=default_days,
    )
    default_end = date.today() - timedelta(days=1)

    start_date = st.sidebar.date_input(
        "Start date",
        value=default_start,
        key="date_start",
    )
    end_date_inclusive = st.sidebar.date_input(
        "End date (inclusive)",
        value=default_end,
        key="date_end",
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
    )
