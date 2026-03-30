"""Tab 1: Spatial Map -- single heatmap with date/mean toggle."""

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
) -> tuple[float | None, float | None]:
    """Render scale-adjustment controls.

    Returns ``(vis_min, vis_max)`` — both *None*
    when the user keeps the default scale.
    """
    cfg = _get_cfg(data_key, source)
    scale = cfg.display_scale
    unit = cfg.display_unit

    min_key = "vis_min"
    max_key = "vis_max"

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
            key="auto_scale",
        )

        # Detect toggle change
        prev_auto = st.session_state.get(
            "_prev_auto_scale",
        )
        toggled = auto != prev_auto
        st.session_state["_prev_auto_scale"] = auto

        if auto and toggled:
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
                st.session_state["auto_scale"] = False
                st.session_state[
                    "_prev_auto_scale"
                ] = False

        if not auto and toggled:
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

    # ── Mode toggle ──────────────────────────────────
    mode = st.radio(
        "Composite type",
        options=["Date composite", "Mean composite"],
        horizontal=True,
        key="heatmap_mode",
    )

    # ── Date controls (only for date mode) ───────────
    selected_date = None
    half_window = 0
    window_label = ""

    available_dates = sorted(
        chart_df["date"].dt.date.unique(),
    )

    if mode == "Date composite":
        if len(available_dates) < 2:
            st.info(
                "Need at least 2 dates to "
                "use the date composite."
            )
            return

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
    else:
        st.caption(
            f"Composite mean of all {sat} passes "
            f"from {hp['start_date']} to "
            f"{hp['end_date']}"
        )

    # ── Scale controls ───────────────────────────────
    vis_min, vis_max = _scale_controls(
        data_key, source, hp,
    )

    # ── Render heatmap ───────────────────────────────
    try:
        if mode == "Date composite":
            with st.spinner(
                f"Loading {data_key} heatmap for "
                f"{window_label}..."
            ):
                tile_url = cached_date_tile_url(
                    data_key,
                    hp["west"],
                    hp["south"],
                    hp["east"],
                    hp["north"],
                    selected_date.isoformat(),
                    half_window,
                    source=source,
                    vis_min=vis_min,
                    vis_max=vis_max,
                )
            layer_name = (
                f"{data_key} {window_label}"
            )
        else:
            with st.spinner(
                f"Loading mean {data_key} heatmap..."
            ):
                tile_url = cached_mean_tile_url(
                    data_key,
                    hp["west"],
                    hp["south"],
                    hp["east"],
                    hp["north"],
                    hp["start_date"],
                    hp["end_date"],
                    source=source,
                    vis_min=vis_min,
                    vis_max=vis_max,
                )
            layer_name = f"Mean {data_key}"

        heatmap = create_heatmap_folium(
            tile_url=tile_url,
            center_lat=center_lat,
            center_lon=center_lon,
            bounds=bounds,
            layer_name=layer_name,
            source=source,
        )
        st_folium(
            heatmap,
            key=(
                f"heatmap_{mode}"
                f"_{vis_min}_{vis_max}"
            ),
            height=500,
            width=None,
        )
    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not render heatmap.",
        )

    render_color_legend(
        data_key, source,
        vis_min=vis_min,
        vis_max=vis_max,
    )

    # ── Store current heatmap state for export ───────
    st.session_state["current_heatmap"] = {
        "mode": mode,
        "selected_date": (
            selected_date.isoformat()
            if selected_date
            else None
        ),
        "half_window": half_window,
        "vis_min": vis_min,
        "vis_max": vis_max,
    }

    # ── Image export ─────────────────────────────────
    _render_image_export(hp, data_key, source)


def _render_image_export(
    hp: dict,
    data_key: str,
    source: str,
) -> None:
    """Render image export controls below the heatmap."""
    import urllib.request

    from app.analysis import (
        cached_thumb_url,
        cached_date_thumb_url,
        cached_download_url,
    )

    hm = st.session_state.get("current_heatmap", {})
    mode = hm.get("mode", "Mean composite")

    with st.expander("Export Image"):
        img_type = st.selectbox(
            "Format",
            options=["PNG", "JPEG", "GeoTIFF"],
            key="export_format",
        )

        if img_type in ("PNG", "JPEG"):
            dimensions = st.slider(
                "Image size (longest edge, px)",
                min_value=256,
                max_value=4096,
                value=1024,
                step=256,
                key="export_dimensions",
                help=(
                    "Controls the longest edge of "
                    "the exported image in pixels."
                ),
            )

            fmt_map = {"PNG": "png", "JPEG": "jpg"}
            fmt = fmt_map[img_type]
            mime = (
                "image/png" if fmt == "png"
                else "image/jpeg"
            )
            ext = "png" if fmt == "png" else "jpeg"

            if st.button(
                f"Generate {img_type}",
                key=f"gen_{fmt}",
            ):
                try:
                    with st.spinner(
                        "Generating image..."
                    ):
                        if mode == "Date composite":
                            thumb_url = (
                                cached_date_thumb_url(
                                    data_key,
                                    hp["west"],
                                    hp["south"],
                                    hp["east"],
                                    hp["north"],
                                    hm["selected_date"],
                                    hm["half_window"],
                                    source=source,
                                    vis_min=hm.get(
                                        "vis_min",
                                    ),
                                    vis_max=hm.get(
                                        "vis_max",
                                    ),
                                    dimensions=dimensions,
                                    img_format=fmt,
                                )
                            )
                        else:
                            thumb_url = (
                                cached_thumb_url(
                                    data_key,
                                    hp["west"],
                                    hp["south"],
                                    hp["east"],
                                    hp["north"],
                                    hp["start_date"],
                                    hp["end_date"],
                                    source=source,
                                    vis_min=hm.get(
                                        "vis_min",
                                    ),
                                    vis_max=hm.get(
                                        "vis_max",
                                    ),
                                    dimensions=dimensions,
                                    img_format=fmt,
                                )
                            )
                        with urllib.request.urlopen(
                            thumb_url, timeout=60,
                        ) as resp:
                            img_bytes = resp.read()

                    st.image(
                        img_bytes,
                        caption=(
                            f"{data_key} composite"
                        ),
                    )
                    fname = (
                        f"openearth_{data_key}"
                        f"_{hp['start_date']}"
                        f"_{hp['end_date']}"
                        f".{ext}"
                    )
                    st.download_button(
                        label=f"Download {img_type}",
                        data=img_bytes,
                        file_name=fname,
                        mime=mime,
                        key=f"dl_{fmt}",
                    )
                except ee.EEException as exc:
                    show_ee_error(
                        exc,
                        "Could not generate image.",
                    )

        elif img_type == "GeoTIFF":
            st.info(
                "GeoTIFF exports the raw raster data "
                "(single band) for use in GIS software "
                "such as QGIS or ArcGIS."
            )
            if st.button(
                "Generate GeoTIFF link",
                key="gen_tiff",
            ):
                try:
                    with st.spinner(
                        "Preparing GeoTIFF..."
                    ):
                        dl_url = cached_download_url(
                            data_key,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            hp["start_date"],
                            hp["end_date"],
                            source=source,
                        )
                    st.markdown(
                        f"[Download GeoTIFF]({dl_url})"
                    )
                except ee.EEException as exc:
                    show_ee_error(
                        exc,
                        "Could not generate GeoTIFF.",
                    )
