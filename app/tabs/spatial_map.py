"""Tab 1: Spatial Map -- date-slider heatmap and mean composite."""

from __future__ import annotations

from datetime import date
from typing import cast

import ee
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from openearth.providers.gee_session import initialize_ee
from openearth.providers.s2_registry import get_s2_index_config
from openearth.providers.s5p_registry import get_gas_config
from openearth.visualization.trace_gas_heatmap import (
    create_heatmap_folium,
)

from app.analysis import (
    cached_date_tile_url,
    cached_mean_tile_url,
    cached_vis_range,
    render_color_legend,
    show_ee_error,
)
from app.roi import map_center

_SAT_LABEL = {
    "s5p": "Sentinel-5P",
    "s2": "Sentinel-2",
}


def _get_cfg(data_key: str, source: str):
    if source == "s2":
        return get_s2_index_config(data_key)
    return get_gas_config(data_key)


def _scale_controls(
    data_key: str,
    source: str,
    hp: dict,
    prefix: str,
) -> tuple[float | None, float | None]:
    """Render scale-adjustment controls.

    Returns ``(vis_min, vis_max)`` — both *None*
    when the user keeps the default scale.
    """
    cfg = _get_cfg(data_key, source)
    scale = cfg.display_scale
    unit = cfg.display_unit

    min_key = f"{prefix}_vis_min"
    max_key = f"{prefix}_vis_max"

    # Initialise slider session state on first run.
    if min_key not in st.session_state:
        st.session_state[min_key] = (
            cfg.vis_min * scale
        )
    if max_key not in st.session_state:
        st.session_state[max_key] = (
            cfg.vis_max * scale
        )

    with st.expander("Scale settings"):
        auto = st.checkbox(
            "Auto-compute from data",
            key=f"{prefix}_auto_scale",
        )

        # Detect toggle change
        prev_key = f"{prefix}_prev_auto"
        prev_auto = st.session_state.get(prev_key)
        toggled = auto != prev_auto
        st.session_state[prev_key] = auto

        if auto and toggled:
            # Just switched ON → compute and
            # force sliders to the new range.
            try:
                with st.spinner(
                    "Computing data range..."
                ):
                    auto_min, auto_max = (
                        cached_vis_range(
                            data_key,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            hp["start_date"],
                            hp["end_date"],
                            source=source,
                        )
                    )
                st.session_state[min_key] = (
                    auto_min * scale
                )
                st.session_state[max_key] = (
                    auto_max * scale
                )
            except ee.EEException:
                st.warning(
                    "Auto-scale computation failed "
                    "(ROI may be too large). "
                    "Using default range."
                )
                st.session_state[
                    f"{prefix}_auto_scale"
                ] = False
                st.session_state[prev_key] = False

        if not auto and toggled:
            # Just switched OFF → reset to defaults.
            st.session_state[min_key] = (
                cfg.vis_min * scale
            )
            st.session_state[max_key] = (
                cfg.vis_max * scale
            )

        st.slider(
            f"Min ({unit})",
            min_value=cfg.valid_min * scale,
            max_value=cfg.valid_max * scale,
            key=min_key,
        )
        st.slider(
            f"Max ({unit})",
            min_value=cfg.valid_min * scale,
            max_value=cfg.valid_max * scale,
            key=max_key,
        )

    raw_min = st.session_state[min_key] / scale
    raw_max = st.session_state[max_key] / scale

    uses_default = (
        abs(raw_min - cfg.vis_min) < 1e-12
        and abs(raw_max - cfg.vis_max) < 1e-12
    )
    if uses_default:
        return (None, None)
    return (raw_min, raw_max)


def render(
    chart_df: pd.DataFrame,
    authenticate_on_fail: bool,
) -> None:
    if "heatmap_params" not in st.session_state:
        st.info("Run an analysis first.")
        return

    hp = st.session_state["heatmap_params"]
    data_key = hp["data_key"]
    source = hp.get("source", "s5p")
    sat = _SAT_LABEL.get(source, "Sentinel-5P")

    try:
        initialize_ee(
            project_id=hp["project_id"],
            authenticate=authenticate_on_fail,
        )
    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not initialize Earth Engine "
            "for map rendering.",
        )
        st.stop()

    center_lat, center_lon = map_center(
        hp["west"], hp["south"],
        hp["east"], hp["north"],
    )
    bounds = [
        [hp["south"], hp["west"]],
        [hp["north"], hp["east"]],
    ]

    # ── Date-slider heatmap ─────────────────────────
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

        date_vmin, date_vmax = _scale_controls(
            data_key, source, hp,
            prefix="date",
        )

        try:
            with st.spinner(
                "Loading date heatmap..."
            ):
                date_tile_url = (
                    cached_date_tile_url(
                        data_key,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        selected_date.isoformat(),
                        half_window,
                        source=source,
                        vis_min=date_vmin,
                        vis_max=date_vmax,
                    )
                )
            date_map = create_heatmap_folium(
                tile_url=date_tile_url,
                center_lat=center_lat,
                center_lon=center_lon,
                bounds=bounds,
                layer_name=(
                    f"{data_key} {window_label}"
                ),
                source=source,
            )
            st_folium(
                date_map,
                key=(
                    f"date_heatmap"
                    f"_{date_vmin}_{date_vmax}"
                ),
                height=500,
                width=None,
            )
        except ee.EEException as exc:
            show_ee_error(
                exc,
                "Could not render date heatmap.",
            )

        render_color_legend(
            data_key, source,
            vis_min=date_vmin,
            vis_max=date_vmax,
        )
    else:
        st.info(
            "Need at least 2 dates to "
            "use the date slider."
        )

    # ── Mean heatmap ────────────────────────────────
    st.subheader("Mean Spatial Distribution")
    st.caption(
        f"Composite mean of all {sat} passes "
        f"from {hp['start_date']} to "
        f"{hp['end_date']}"
    )

    mean_vmin, mean_vmax = _scale_controls(
        data_key, source, hp,
        prefix="mean",
    )

    try:
        with st.spinner(
            "Loading mean heatmap..."
        ):
            mean_tile_url = (
                cached_mean_tile_url(
                    data_key,
                    hp["west"],
                    hp["south"],
                    hp["east"],
                    hp["north"],
                    hp["start_date"],
                    hp["end_date"],
                    source=source,
                    vis_min=mean_vmin,
                    vis_max=mean_vmax,
                )
            )
        mean_map = create_heatmap_folium(
            tile_url=mean_tile_url,
            center_lat=center_lat,
            center_lon=center_lon,
            bounds=bounds,
            layer_name=f"Mean {data_key}",
            source=source,
        )
        st_folium(
            mean_map,
            key=(
                f"mean_heatmap"
                f"_{mean_vmin}_{mean_vmax}"
            ),
            height=500,
            width=None,
        )
    except Exception as exc:
        show_ee_error(
            exc,
            "Could not render mean heatmap.",
        )

    render_color_legend(
        data_key, source,
        vis_min=mean_vmin,
        vis_max=mean_vmax,
    )
