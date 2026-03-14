"""OpenEarth Explorer – dashboard app."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, cast

import ee
import pandas as pd
import streamlit as st
import folium

from folium.plugins import Draw
from streamlit_folium import st_folium

from openearth.analytics.smoothing import add_rolling_no2
from openearth.analytics.trace_gas_daily import (
    build_daily_timeseries,
    BATCH_SIZE  # is halved if ROI to big
)
from openearth.providers.gas_registry import (
    GAS_REGISTRY,
    get_gas_config,
)
from openearth.providers.gee_session import initialize_ee
from openearth.visualization.trace_gas_heatmap import (
    build_mean_composite,
    build_date_composite,
    get_tile_url,
    create_heatmap_folium,
)

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
    # Cities
    "Heidelberg (Germany)": (8.58, 49.35, 8.77, 49.46),
    "London (UK)": (-0.51, 51.28, 0.33, 51.70),
    "Berlin (Germany)": (13.09, 52.33, 13.76, 52.68),
    "New York (USA)": (-74.26, 40.49, -73.69, 40.92),
    "Merida (Mexico)": (-89.80, 20.85, -89.50, 21.10),
    "Barranquilla (Colombia)": (
        -74.93, 10.90, -74.70, 11.10,
    )
}
DEFAULT_EXAMPLE = "Europe"

TRACE_GASES: dict[str, str] = {
    k: cfg.name for k, cfg in GAS_REGISTRY.items()
}

# ── Helper functions ───────────────────────────────────────────


def _set_bbox(
    west: float, south: float,
    east: float, north: float,
) -> None:
    st.session_state["roi_west"] = west
    st.session_state["roi_south"] = south
    st.session_state["roi_east"] = east
    st.session_state["roi_north"] = north


def _init_bbox_state() -> None:
    if "roi_west" in st.session_state:
        return
    w, s, e, n = ROI_EXAMPLES[DEFAULT_EXAMPLE]
    _set_bbox(w, s, e, n)


def _apply_pending_bbox() -> None:
    pending = st.session_state.pop("pending_bbox", None)
    if not pending:
        return
    west, south, east, north = pending
    _set_bbox(west, south, east, north)


def _bbox_from_geometry(
    geometry: dict[str, Any] | None,
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


def _map_center(
    west: float, south: float,
    east: float, north: float,
) -> tuple[float, float]:
    return (
        (south + north) / 2.0,
        (west + east) / 2.0,
    )


def _render_color_legend(gas_key: str) -> None:
    """Render an HTML color bar legend for *gas_key*."""
    cfg = get_gas_config(gas_key)
    gradient_css = ", ".join(cfg.palette)
    label_min = cfg.vis_min * cfg.display_scale
    label_max = cfg.vis_max * cfg.display_scale
    unit = cfg.display_unit
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;
                    margin:8px 0 16px 0;">
            <span style="font-size:0.85em;
                         margin-right:8px;">
                {label_min:.4g} {unit}
            </span>
            <div style="
                flex:1; height:16px;
                background:linear-gradient(
                    to right, {gradient_css});
                border-radius:4px;
                border:1px solid #ccc;
            "></div>
            <span style="font-size:0.85em;
                         margin-left:8px;">
                {label_max:.4g} {unit}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_roi_draw_map(
    west: float, south: float,
    east: float, north: float,
) -> None:
    st.subheader("Draw ROI on Map")
    center_lat, center_lon = _map_center(
        west, south, east, north,
    )
    fmap = folium.Map(
        location=[center_lat, center_lon],
        tiles="CartoDB positron",
    )
    fmap.fit_bounds([[south, west], [north, east]])
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
        edit_options={
            "edit": True, "remove": True,
        },
    ).add_to(fmap)

    map_state = st_folium(
        fmap,
        key="roi_draw_map",
        height=430,
        use_container_width=True,
        returned_objects=["last_active_drawing"],
    )
    drawing = (
        map_state.get("last_active_drawing")
        if isinstance(map_state, dict)
        else None
    )
    drawing_geom = (
        drawing.get("geometry")
        if isinstance(drawing, dict)
        else None
    )
    drawn_bbox = _bbox_from_geometry(drawing_geom)
    if drawn_bbox is None:
        st.caption(
            "Draw a rectangle or polygon, "
            "then click `Use drawn ROI`."
        )
        return

    dw, ds, de, dn = drawn_bbox
    st.caption(
        f"Drawn ROI: W {dw:.4f}, "
        f"S {ds:.4f}, "
        f"E {de:.4f}, "
        f"N {dn:.4f}"
    )
    if st.button("Use drawn ROI"):
        st.session_state["pending_bbox"] = (
            dw, ds, de, dn,
        )
        st.rerun()


def _analysis_cache_key(
    gas_key: str,
    start_date_iso: str,
    end_date_iso: str,
    west: float,
    south: float,
    east: float,
    north: float,
    project_id: str,
) -> tuple[Any, ...]:
    return (
        gas_key,
        start_date_iso,
        end_date_iso,
        round(west, 6),
        round(south, 6),
        round(east, 6),
        round(north, 6),
        project_id.strip(),
    )


def _heatmap_params(
    gas_key: str,
    start_date_iso: str,
    end_date_iso: str,
    west: float,
    south: float,
    east: float,
    north: float,
    project_id: str,
) -> dict[str, Any]:
    return {
        "gas_key": gas_key,
        "start_date": start_date_iso,
        "end_date": end_date_iso,
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "project_id": project_id,
    }


def _classify_ee_error(
    exc: Exception,
) -> tuple[str, str]:
    message = str(exc).lower()

    auth_tokens = (
        "auth",
        "authenticate",
        "credential",
        "permission",
        "not authorized",
        "forbidden",
        "access denied",
        "401",
        "403",
        "token",
    )
    quota_tokens = (
        "too many concurrent",
        "quota",
        "rate",
        "limit exceeded",
        "429",
        "timed out",
        "user memory limit exceeded",
    )
    empty_tokens = (
        "collection is empty",
        "no images",
        "contains no images",
        "empty collection",
        "0 elements",
        "no valid pixels",
    )

    if any(token in message for token in auth_tokens):
        return (
            "auth",
            "Earth Engine authentication or permissions failed. "
            "Check project access and sign in again.",
        )
    if any(token in message for token in quota_tokens):
        return (
            "quota",
            "Earth Engine quota or concurrency limit reached. "
            "Try a smaller ROI/date range or retry shortly.",
        )
    if any(token in message for token in empty_tokens):
        return (
            "empty",
            "No satellite observations are available for this "
            "gas, ROI, and time window.",
        )

    return (
        "unknown",
        "Unexpected Earth Engine error.",
    )


def _show_ee_error(
    exc: Exception,
    context: str,
) -> None:
    category, user_message = _classify_ee_error(exc)
    full_message = f"{context} {user_message}"

    if category == "auth":
        st.error(full_message)
        return
    if category == "quota":
        st.warning(full_message)
        return
    if category == "empty":
        st.info(full_message)
        return

    st.error(full_message)
    st.exception(exc)


# ── Cached tile helpers ────────────────────────────────────────


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_mean_tile_url(
    gas_key: str,
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
) -> str:
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_mean_composite(
        gas_key, roi, start_date, end_date,
    )
    return get_tile_url(image, gas_key)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_date_tile_url(
    gas_key: str,
    west: float, south: float,
    east: float, north: float,
    target_date: str,
    half_window_days: int,
) -> str:
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_date_composite(
        gas_key, roi, target_date,
        half_window_days,
    )
    return get_tile_url(image, gas_key)


# ── Page config ────────────────────────────────────────────────

st.set_page_config(
    page_title="OpenEarth Explorer",
    layout="wide",
)
st.title("OpenEarth Explorer")
st.caption(
    "Satellite-based atmospheric analysis "
    "for user-defined regions."
)

_init_bbox_state()
_apply_pending_bbox()

# ── Sidebar: global settings ──────────────────────────────────

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
    _set_bbox(*ROI_EXAMPLES[selected_example])
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

# ── ROI map (always visible) ──────────────────────────────────

_render_roi_draw_map(west, south, east, north)

# ── Run analysis ──────────────────────────────────────────────

if run:
    if not project_id.strip():
        st.error("Project ID is required.")
        st.stop()
    if east <= west or north <= south:
        st.error(
            "Invalid bounding box. "
            "Ensure east > west and north > south."
        )
        st.stop()
    if end_date_inclusive < start_date:
        st.error(
            "End date must be on or after "
            "start date."
        )
        st.stop()

    end_date_exclusive = (
        end_date_inclusive + timedelta(days=1)
    )
    start_date_iso = start_date.isoformat()
    end_date_iso = end_date_exclusive.isoformat()

    cache_key = _analysis_cache_key(
        gas_key=selected_gas,
        start_date_iso=start_date_iso,
        end_date_iso=end_date_iso,
        west=west,
        south=south,
        east=east,
        north=north,
        project_id=project_id,
    )
    analysis_cache = st.session_state.setdefault(
        "analysis_cache", {}
    )

    cached_entry = analysis_cache.get(cache_key)
    if isinstance(cached_entry, dict):
        cached_df = cached_entry.get("df")
        if isinstance(cached_df, pd.DataFrame):
            st.session_state["analysis_df"] = cached_df.copy()
            st.session_state["heatmap_params"] = (
                _heatmap_params(
                    gas_key=selected_gas,
                    start_date_iso=start_date_iso,
                    end_date_iso=end_date_iso,
                    west=west,
                    south=south,
                    east=east,
                    north=north,
                    project_id=project_id,
                )
            )
            st.toast("Loaded cached analysis")
            st.rerun()

    gas_cfg = get_gas_config(selected_gas)

    try:
        with st.spinner(
            "Initializing Earth Engine...",
        ):
            try:
                initialize_ee(
                    project_id=project_id,
                    authenticate=authenticate_on_fail,
                )
            except Exception as exc:
                _show_ee_error(
                    exc,
                    "Could not initialize Earth Engine."
                )
                st.stop()

        with st.spinner(
            f"Building {gas_cfg.key} time series...",
        ):
            roi = ee.Geometry.BBox(
                west, south, east, north,
            )

            batch_sz = BATCH_SIZE
            while True:
                try:
                    df = build_daily_timeseries(
                        gas_key=selected_gas,
                        geometry=roi,
                        start_date=start_date_iso,
                        end_date=end_date_iso,
                        batch_size=batch_sz,
                    )
                except Exception as e:
                    is_agg_err = (
                        "Too many concurrent"
                        in str(e)
                    )
                    if is_agg_err and batch_sz >= 2:
                        batch_sz = batch_sz // 2
                        st.toast(
                            "Reducing batch size "
                            f"to {batch_sz}..."
                        )
                        continue
                    raise
                break

    except Exception as exc:
        _show_ee_error(
            exc,
            "Analysis failed."
        )
        st.stop()

    # st.markdown(f"**Batch Size: {BATCH_SIZE}**")

    if df.empty:
        st.info(
            "No observations found for the selected "
            "gas, ROI, and date range."
        )
        st.stop()

    st.session_state["analysis_df"] = df
    st.session_state["heatmap_params"] = (
        _heatmap_params(
            gas_key=selected_gas,
            start_date_iso=start_date_iso,
            end_date_iso=end_date_iso,
            west=west,
            south=south,
            east=east,
            north=north,
            project_id=project_id,
        )
    )

    analysis_cache[cache_key] = {
        "df": df.copy()
    }
    max_cache_entries = 5
    if len(analysis_cache) > max_cache_entries:
        oldest_key = next(iter(analysis_cache))
        analysis_cache.pop(oldest_key, None)

# ── Guard: stop if no results yet ─────────────────────────────

if "analysis_df" not in st.session_state:
    st.info(
        "Configure inputs in the sidebar "
        "and click **Run analysis**."
    )
    st.stop()

# ── Tabs ───────────────────────────────────────────────────────

(
    tab_spatial,
    tab_timeseries,
    tab_compare,
    tab_stats,
    tab_animation,
    tab_image,
) = st.tabs([
    "Spatial Map",
    "Time Series",
    "Compare",
    "Statistics",
    "Animation",
    "Image"
])

chart_df = st.session_state["analysis_df"].copy()
chart_df["date"] = pd.to_datetime(chart_df["date"])

# ── Tab 1: Spatial Map ─────────────────────────────────────────

with tab_spatial:
    if "heatmap_params" not in st.session_state:
        st.info("Run an analysis first.")
    else:
        hp = st.session_state["heatmap_params"]
        gas_key = hp["gas_key"]

        try:
            initialize_ee(
                project_id=hp["project_id"],
                authenticate=authenticate_on_fail,
            )
        except Exception as exc:
            _show_ee_error(
                exc,
                "Could not initialize Earth Engine for map rendering."
            )
            st.stop()

        center_lat, center_lon = _map_center(
            hp["west"], hp["south"],
            hp["east"], hp["north"],
        )
        bounds = [
            [hp["south"], hp["west"]],
            [hp["north"], hp["east"]],
        ]

        # Date-slider heatmap
        st.subheader("Explore by Date")

        available_dates = sorted(
            chart_df["date"].dt.date.unique(),
        )
        if len(available_dates) >= 2:
            selected_date = cast(
                date,
                st.select_slider(
                    "Select date",
                    options=available_dates,
                    value=available_dates[
                        len(available_dates) // 2
                    ],
                    key="heatmap_date_slider",
                ),
            )
            half_window = st.slider(
                "Composite window (+/- days)",
                min_value=0,
                max_value=14,
                value=7,
                help=(
                    "Days before and after the "
                    "selected date to include."
                ),
                key="heatmap_half_window",
            )
            window_label = (
                f"{selected_date}"
                if half_window == 0
                else (
                    f"{selected_date} "
                    f"+/- {half_window} days"
                )
            )
            st.caption(f"Showing: {window_label}")

            try:
                with st.spinner(
                    "Loading date heatmap..."
                ):
                    date_tile_url = (
                        _cached_date_tile_url(
                            gas_key,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            selected_date.isoformat(),
                            half_window,
                        )
                    )
                date_map = create_heatmap_folium(
                    tile_url=date_tile_url,
                    center_lat=center_lat,
                    center_lon=center_lon,
                    bounds=bounds,
                    layer_name=(
                        f"{gas_key} {window_label}"
                    ),
                )
                st_folium(
                    date_map,
                    key="date_heatmap",
                    height=500,
                    use_container_width=True,
                )
            except Exception as exc:
                _show_ee_error(
                    exc,
                    "Could not render date heatmap."
                )

            _render_color_legend(gas_key)
        else:
            st.info(
                "Need at least 2 dates to "
                "use the date slider."
            )

        # Mean heatmap
        st.subheader("Mean Spatial Distribution")
        st.caption(
            "Composite mean of all "
            "Sentinel-5P passes from "
            f"{hp['start_date']} to "
            f"{hp['end_date']}"
        )

        try:
            with st.spinner(
                "Loading mean heatmap..."
            ):
                mean_tile_url = (
                    _cached_mean_tile_url(
                        gas_key,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                    )
                )
            mean_map = create_heatmap_folium(
                tile_url=mean_tile_url,
                center_lat=center_lat,
                center_lon=center_lon,
                bounds=bounds,
                layer_name=f"Mean {gas_key}",
            )
            st_folium(
                mean_map,
                key="mean_heatmap",
                height=500,
                use_container_width=True,
            )
        except Exception as exc:
            _show_ee_error(
                exc,
                "Could not render mean heatmap."
            )

        _render_color_legend(gas_key)

# ── Tab 2: Time Series ────────────────────────────────────────

with tab_timeseries:
    ts_cfg, ts_plot = st.columns([1, 3])

    with ts_cfg:
        st.markdown("**Smoothing**")
        window_days = st.slider(
            "Window (days)",
            min_value=3,
            max_value=30,
            value=14,
            key="ts_window_days",
        )
        min_periods = st.slider(
            "Min valid days",
            min_value=1,
            max_value=window_days,
            value=min(4, window_days),
            key="ts_min_periods",
        )
        smoothing_method = st.selectbox(
            "Method",
            options=["mean", "median"],
            index=0,
            key="ts_method",
        )

        st.markdown("**Series**")
        show_raw = st.checkbox(
            "Raw values", value=True,
            key="ts_show_raw",
        )
        show_smooth = st.checkbox(
            "Smoothed", value=True,
            key="ts_show_smooth",
        )

    ts_df = chart_df.copy()
    if show_smooth:
        ts_df = add_rolling_no2(
            ts_df,
            value_col="value",
            window_days=window_days,
            min_periods=min_periods,
            method=smoothing_method,
            output_col="smoothed",
        )

    plot_cols: list[str] = []
    if show_raw:
        plot_cols.append("value")
    if show_smooth:
        plot_cols.append("smoothed")

    with ts_plot:
        st.subheader(
            f"Daily {selected_gas} Time Series",
        )
        if not plot_cols:
            st.warning(
                "Select at least one series."
            )
        else:
            st.line_chart(
                ts_df.set_index("date")[plot_cols],
                use_container_width=True,
            )

    coverage_mean = pd.to_numeric(
        chart_df["coverage_fraction"],
        errors="coerce",
    ).mean()

    with st.expander("Coverage", expanded=False):
        st.area_chart(
            chart_df.set_index("date")[
                ["coverage_fraction"]
            ],
            use_container_width=True,
        )
        st.caption(
            f"Rows: {len(chart_df)} | "
            f"Mean coverage: {coverage_mean:.2%}"
        )

    with st.expander("Data", expanded=False):
        st.dataframe(
            chart_df, use_container_width=True,
        )
        export_df = chart_df.copy()
        export_df["date"] = pd.to_datetime(
            export_df["date"]
        ).dt.strftime("%Y-%m-%d")
        csv_data = export_df.to_csv(
            index=False
        ).encode("utf-8")

        date_min = export_df["date"].min()
        date_max = export_df["date"].max()
        file_name = (
            f"openearth_{selected_gas.lower()}_"
            f"{date_min}_{date_max}.csv"
        )

        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name=file_name,
            mime="text/csv",
            key="exp_csv",
        )

# ── Tab 3: Compare (placeholder) ──────────────────────────────

with tab_compare:
    st.subheader("Compare")
    st.info(
        "**Coming soon** – Compare two trace gases "
        "or two regions side by side over the same "
        "time period."
    )

    c1, c2 = st.columns(2)
    with c1:
        st.selectbox(
            "Region / Gas A",
            options=[
                f"Current ROI – {selected_gas}"
            ],
            disabled=True,
            key="cmp_a",
        )
        st.empty()
    with c2:
        st.selectbox(
            "Region / Gas B",
            options=["Select..."],
            disabled=True,
            key="cmp_b",
        )
        st.empty()

# ── Tab 4: Statistics ────────────────────────────

with tab_stats:
    st.subheader("Statistics")
    cfg = get_gas_config(selected_gas)
    stats_dates = pd.to_datetime(
        chart_df["date"], errors="coerce"
    )
    stats_values = pd.to_numeric(
        chart_df["value"], errors="coerce"
    )
    valid = stats_dates.notna() & stats_values.notna()

    if not valid.any():
        st.warning(
            "No valid values available for "
            "statistics in the selected range."
        )
    else:
        valid_dates = stats_dates[valid]
        valid_values = stats_values[valid]

        mean_val = valid_values.mean()
        median_val = valid_values.median()
        max_idx = valid_values.idxmax()
        max_val = float(valid_values.loc[max_idx])
        max_date = pd.to_datetime(
            chart_df.loc[max_idx, "date"]
        ).date()

        x_days = (
            valid_dates - valid_dates.min()
        ).dt.days.astype(float)
        trend_value = "Stable"
        trend_delta = "n/a"

        if x_days.nunique() >= 2:
            x_centered = x_days - x_days.mean()
            y_centered = (
                valid_values - valid_values.mean()
            )
            denominator = (
                x_centered.pow(2).sum()
            )
            if denominator > 0:
                slope_per_day = float(
                    (x_centered * y_centered).sum()
                    / denominator
                )
                slope_per_week = slope_per_day * 7.0
                stability_tol = max(
                    abs(float(mean_val)) * 0.005,
                    1e-12,
                )

                if slope_per_week > stability_tol:
                    trend_value = "Increasing"
                elif slope_per_week < -stability_tol:
                    trend_value = "Decreasing"

                trend_delta = (
                    f"{slope_per_week * cfg.display_scale:+.4g} "
                    f"{cfg.display_unit}/week"
                )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "Mean",
            f"{mean_val * cfg.display_scale:.4g} {cfg.display_unit}",
        )
        m2.metric(
            "Median",
            f"{median_val * cfg.display_scale:.4g} {cfg.display_unit}",
        )
        m3.metric(
            "Max",
            f"{max_val * cfg.display_scale:.4g} {cfg.display_unit}",
            delta=f"on {max_date.isoformat()}",
        )
        m4.metric(
            "Trend",
            trend_value,
            delta=trend_delta,
        )

        st.caption(
            f"Computed from {int(valid.sum())} valid "
            "daily observations."
        )

# ── Tab 5: Export (placeholder) ────────────────────────────────

with tab_animation:
    st.subheader("Animation")
    st.info(
        "**Coming soon** – Create and download an animated heatmap "
        "visualising the atmospheric flow"
    )

    st.button(
        "Download Animation",
        disabled=True,
        key="exp_anim",
    )

with tab_image:
    st.subheader("Create Image")
    st.info(
        "**Coming soon** - Download heatmaps as GeoTIFF "
        "or other image file types"
    )
    ImgType = st.selectbox(
        label="Select Image Type",
        options=["PNG", "JPEG", "GeoTIFF"]
        )

    if ImgType == "GeoTIFF":
        st.button(
            "Download GeoTIFF composite",
            disabled=True,
            key="exp_tiff"
        )
    elif ImgType == "PNG":
        st.button(
            "Download PNG composite",
            disabled=True,
            key="exp_png",
        )
    elif ImgType == "JPEG":
        st.button(
            "Download JPEG composite",
            disabled=True,
            key="exp_jpeg",
        )
