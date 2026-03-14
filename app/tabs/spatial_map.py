"""Tab 1: Spatial Map – date-slider heatmap and mean composite."""

from __future__ import annotations

from datetime import date
from typing import cast

import ee
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from openearth.providers.gee_session import initialize_ee
from openearth.visualization.trace_gas_heatmap import create_heatmap_folium

from app.analysis import (
    cached_date_tile_url,
    cached_mean_tile_url,
    render_color_legend,
    show_ee_error,
)
from app.roi import map_center


def render(
    chart_df: pd.DataFrame,
    authenticate_on_fail: bool,
) -> None:
    if "heatmap_params" not in st.session_state:
        st.info("Run an analysis first.")
        return

    hp = st.session_state["heatmap_params"]
    gas_key = hp["gas_key"]

    try:
        initialize_ee(
            project_id=hp["project_id"],
            authenticate=authenticate_on_fail,
        )
    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not initialize Earth Engine for map rendering."
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

    # ── Date-slider heatmap ───────────────────────────────
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
                    cached_date_tile_url(
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
        except ee.EEException as exc:
            show_ee_error(
                exc,
                "Could not render date heatmap."
            )

        render_color_legend(gas_key)
    else:
        st.info(
            "Need at least 2 dates to "
            "use the date slider."
        )

    # ── Mean heatmap ──────────────────────────────────────
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
                cached_mean_tile_url(
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
        show_ee_error(
            exc,
            "Could not render mean heatmap."
        )

    render_color_legend(gas_key)
