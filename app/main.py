"""OpenEarth NO2 MVP Streamlit app."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import ee
import pandas as pd
import streamlit as st
import folium

from folium.plugins import Draw
from streamlit_folium import st_folium

from openearth.analytics.no2_daily import build_no2_daily_timeseries
from openearth.analytics.smoothing import add_rolling_no2
from openearth.providers.gee_session import initialize_ee

ROI_EXAMPLES: dict[str, tuple[float, float, float, float]] = {
    "Heidelberg (Germany)": (8.58, 49.35, 8.77, 49.46),
    "London (UK)": (-0.51, 51.28, 0.33, 51.70),
    "Berlin (Germany)": (13.09, 52.33, 13.76, 52.68),
    "New York (USA)": (-74.26, 40.49, -73.69, 40.92),
    "Merida (Mexico)": (-89.80, 20.85, -89.50, 21.10),
    "Barranquilla (Colombia)": (-74.93, 10.90, -74.70, 11.10),
}
DEFAULT_EXAMPLE = "Heidelberg (Germany)"


def _set_bbox(west: float, south: float, east: float, north: float) -> None:
    st.session_state["roi_west"] = west
    st.session_state["roi_south"] = south
    st.session_state["roi_east"] = east
    st.session_state["roi_north"] = north


def _init_bbox_state() -> None:
    if "roi_west" in st.session_state:
        return
    west, south, east, north = ROI_EXAMPLES[DEFAULT_EXAMPLE]
    _set_bbox(west, south, east, north)


def _apply_pending_bbox() -> None:
    pending = st.session_state.pop("pending_bbox", None)
    if not pending:
        return
    west, south, east, north = pending
    _set_bbox(west, south, east, north)


def _bbox_from_geometry(geometry: dict[str, Any] | None
                        ) -> tuple[float, float, float, float] | None:
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return None

    lons: list[float] = []
    lats: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            if (
                len(value) >= 2
                and isinstance(value[0], (int, float))
                and isinstance(value[1], (int, float))
            ):
                lons.append(float(value[0]))
                lats.append(float(value[1]))
                return
            for item in value:
                walk(item)

    walk(coordinates)
    if not lons or not lats:
        return None
    return min(lons), min(lats), max(lons), max(lats)


def _map_center(west: float, south: float, east: float, north: float
                ) -> tuple[float, float]:
    return ((south + north) / 2.0, (west + east) / 2.0)


def _map_zoom(west: float, south: float, east: float, north: float) -> int:
    span = max(east - west, north - south)
    if span <= 0.06:
        return 11
    if span <= 0.12:
        return 10
    if span <= 0.30:
        return 9
    if span <= 0.80:
        return 8
    return 6


def _render_roi_draw_map(west: float, south: float, east: float, north: float
                         ) -> None:
    st.subheader("Draw ROI on Map")

    center_lat, center_lon = _map_center(west, south, east, north)
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=_map_zoom(west, south, east, north),
        tiles="CartoDB positron",
    )
    folium.Rectangle(
        bounds=[[south, west], [north, east]],
        color="#1f77b4",
        weight=2,
        fill=False,
        tooltip="Current ROI",
    ).add_to(fmap)
    Draw(
        export=False,
        draw_options={
            "polyline": False,
            "circle": False,
            "marker": False,
            "circlemarker": False,
            "polygon": True,
            "rectangle": True,
        },
        edit_options={"edit": False, "remove": True},
    ).add_to(fmap)

    map_state = st_folium(
        fmap,
        key="roi_draw_map",
        height=430,
        use_container_width=True,
        returned_objects=["last_active_drawing"],
    )
    drawing = map_state.get("last_active_drawing") if isinstance(map_state, dict) else None
    drawing_geom = drawing.get("geometry") if isinstance(drawing, dict) else None
    drawn_bbox = _bbox_from_geometry(drawing_geom)
    if drawn_bbox is None:
        st.caption("Draw a rectangle or polygon, then click `Use drawn ROI`.")
        return

    draw_west, draw_south, draw_east, draw_north = drawn_bbox
    st.caption(
        "Drawn ROI: "
        f"W {draw_west:.4f}, S {draw_south:.4f}, E {draw_east:.4f}, N {draw_north:.4f}"
    )
    if st.button("Use drawn ROI"):
        st.session_state["pending_bbox"] = (draw_west, draw_south, draw_east, draw_north)
        st.rerun()


st.set_page_config(page_title="OpenEarth Explorer", layout="wide")
st.title("OpenEarth Explorer: NO2 MVP")
st.caption("Daily Sentinel-5P NO2 for a user-defined bounding box.")

_init_bbox_state()
_apply_pending_bbox()

st.sidebar.header("Configuration")
project_default = os.getenv("OPENEARTH_EE_PROJECT", "openearth-488015")
project_id = st.sidebar.text_input("Earth Engine project ID",
                                   value=project_default)
authenticate_on_fail = st.sidebar.checkbox(
    "Authenticate on initialization failure", value=True
)

st.sidebar.header("ROI (Bounding Box)")
selected_example = st.sidebar.selectbox(
    "Example regions",
    options=list(ROI_EXAMPLES.keys()),
    index=list(ROI_EXAMPLES.keys()).index(DEFAULT_EXAMPLE),
)
if st.sidebar.button("Load example ROI"):
    _set_bbox(*ROI_EXAMPLES[selected_example])
    st.rerun()

st.sidebar.number_input("West (lon)", key="roi_west", format="%.4f")
st.sidebar.number_input("South (lat)", key="roi_south", format="%.4f")
st.sidebar.number_input("East (lon)", key="roi_east", format="%.4f")
st.sidebar.number_input("North (lat)", key="roi_north", format="%.4f")

west = float(st.session_state["roi_west"])
south = float(st.session_state["roi_south"])
east = float(st.session_state["roi_east"])
north = float(st.session_state["roi_north"])

st.sidebar.header("Time Range")
default_start = date.today() - timedelta(days=365)
default_end = date.today() - timedelta(days=1)
start_date = st.sidebar.date_input("Start date", value=default_start)
end_date_inclusive = st.sidebar.date_input("End date (inclusive)",
                                           value=default_end)

st.sidebar.header("Smoothing")
window_days = st.sidebar.slider("Window (days)",
                                min_value=3, max_value=30, value=14)
min_periods = st.sidebar.slider(
    "Minimum valid days", min_value=1, max_value=window_days, value=min(4, window_days)
)
smoothing_method = st.sidebar.selectbox("Method",
                                        options=["mean", "median"],
                                        index=0)

_render_roi_draw_map(west, south, east, north)

run = st.sidebar.button("Run NO2 analysis", type="primary")

if run:
    if not project_id.strip():
        st.error("Project ID is required.")
        st.stop()
    if east <= west or north <= south:
        st.error("Invalid bounding box. Ensure east > west and north > south.")
        st.stop()
    if end_date_inclusive < start_date:
        st.error("End date must be on or after start date.")
        st.stop()
    end_date_exclusive = end_date_inclusive + timedelta(days=1)

    try:
        with st.spinner("Initializing Earth Engine and computing daily NO2..."):
            initialize_ee(project_id=project_id,
                          authenticate=authenticate_on_fail)
            roi = ee.Geometry.BBox(west, south, east, north)
            df = build_no2_daily_timeseries(
                geometry=roi,
                start_date=start_date.isoformat(),
                end_date=end_date_exclusive.isoformat(),
            )
    except Exception as exc:
        st.exception(exc)
        st.stop()

    if df.empty:
        st.warning("No rows returned for the selected input.")
        st.stop()

    st.session_state["analysis_df"] = df

if "analysis_df" not in st.session_state:
    st.info("Configure inputs in the sidebar and click 'Run NO2 analysis'.")
    st.stop()

chart_df = st.session_state["analysis_df"].copy()
chart_df["date"] = pd.to_datetime(chart_df["date"])

if "show_no2_value" not in st.session_state:
    st.session_state["show_no2_value"] = True
if "show_no2_smoothed" not in st.session_state:
    st.session_state["show_no2_smoothed"] = True

plot_col, toggle_col = st.columns([5, 1])
with toggle_col:
    st.markdown("**Series**")
    show_no2_value = st.checkbox("no2_value", key="show_no2_value")
    show_no2_smoothed = st.checkbox("no2_smoothed", key="show_no2_smoothed")

if show_no2_smoothed:
    chart_df = add_rolling_no2(
        chart_df,
        value_col="no2_value",
        window_days=window_days,
        min_periods=min_periods,
        method=smoothing_method,
        output_col="no2_smoothed",
    )

plot_cols: list[str] = []
if show_no2_value:
    plot_cols.append("no2_value")
if show_no2_smoothed:
    plot_cols.append("no2_smoothed")

with plot_col:
    st.subheader("Daily NO2 Time Series")
    if not plot_cols:
        st.warning("Select at least one series.")
    else:
        st.line_chart(chart_df.set_index("date")[plot_cols],
                      use_container_width=True)

st.subheader("Coverage")
st.area_chart(
    chart_df.set_index("date")[["coverage_fraction"]],
    use_container_width=True,
)

coverage_mean = pd.to_numeric(chart_df["coverage_fraction"],
                              errors="coerce").mean()
st.caption(
    f"Rows: {len(chart_df)} | Mean coverage: {coverage_mean:.2%} | "
    "Date input uses inclusive end date in UI (converted to EE exclusive \
        internally)."
)

st.subheader("Data")
st.dataframe(chart_df, use_container_width=True)
