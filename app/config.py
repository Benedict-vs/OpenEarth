"""Constants and sidebar configuration for OpenEarth Explorer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta

import streamlit as st

from openearth.providers.gas_registry import GAS_REGISTRY


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
    "Berlin (Germany)": (13.09, 52.33, 13.76, 52.68),
    "New York (USA)": (-74.26, 40.49, -73.69, 40.92),
    "Merida (Mexico)": (-89.80, 20.85, -89.50, 21.10),
    "Barranquilla (Colombia)": (
        -74.93, 10.90, -74.70, 11.10,
    ),
}
DEFAULT_EXAMPLE = "Europe"

TRACE_GASES: dict[str, str] = {
    k: cfg.name for k, cfg in GAS_REGISTRY.items()
}


# ── Sidebar output ────────────────────────────────────────────

@dataclass
class SidebarConfig:
    project_id: str
    authenticate_on_fail: bool
    selected_gas: str
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

    st.sidebar.header("Trace Gas")
    selected_gas = st.sidebar.selectbox(
        "Variable",
        options=list(TRACE_GASES.keys()),
        format_func=lambda k: f"{k} – {TRACE_GASES[k]}",
        index=0,
    )

    st.sidebar.header("ROI (Region of Interest)")
    selected_example = st.sidebar.selectbox(
        "Example regions",
        options=list(ROI_EXAMPLES.keys()),
        index=list(ROI_EXAMPLES.keys()).index(
            DEFAULT_EXAMPLE,
        ),
    )
    if st.sidebar.button("Load example ROI"):
        set_bbox(*ROI_EXAMPLES[selected_example])
        st.rerun()

    st.sidebar.number_input(
        "West (lon)", key="roi_west", format="%.4f",
    )
    st.sidebar.number_input(
        "South (lat)", key="roi_south", format="%.4f",
    )
    st.sidebar.number_input(
        "East (lon)", key="roi_east", format="%.4f",
    )
    st.sidebar.number_input(
        "North (lat)", key="roi_north", format="%.4f",
    )

    west = float(st.session_state["roi_west"])
    south = float(st.session_state["roi_south"])
    east = float(st.session_state["roi_east"])
    north = float(st.session_state["roi_north"])

    st.sidebar.header("Time Range")
    default_start = date.today() - timedelta(days=365)
    default_end = date.today() - timedelta(days=1)
    start_date = st.sidebar.date_input(
        "Start date", value=default_start,
    )
    end_date_inclusive = st.sidebar.date_input(
        "End date (inclusive)", value=default_end,
    )

    run = st.sidebar.button(
        "Run analysis", type="primary",
    )

    return SidebarConfig(
        project_id=project_id,
        authenticate_on_fail=authenticate_on_fail,
        selected_gas=selected_gas,
        west=west,
        south=south,
        east=east,
        north=north,
        start_date=start_date,
        end_date_inclusive=end_date_inclusive,
        run=run,
    )
