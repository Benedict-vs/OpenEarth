"""Tab 1: Spatial Map -- multi-layer heatmap with date/mean toggle."""

from __future__ import annotations

import math
from datetime import date
from typing import cast

import ee
import folium
import streamlit as st
from streamlit_folium import st_folium

from openearth.providers import get_config
from openearth.providers.gee_session import initialize_ee
from openearth.visualization.heatmap import (
    LayerSpec,
    create_multilayer_heatmap_folium,
)

from app.analysis import (
    cached_date_tile_url,
    cached_masked_date_tile_url,
    cached_masked_tile_url,
    cached_mean_tile_url,
    cached_methane_anomaly_tile_url,
    cached_source_classification_tile_url,
    cached_vis_range,
    render_classification_legend,
    render_color_legend,
)
from openearth.providers import _resolve_source
from app.errors import show_ee_error
from app.roi import map_center

_SAT_LABEL = {
    "s5p": "Sentinel-5P",
    "s2": "Sentinel-2",
}


def _ee_fetch_timeout(
    dimensions: int,
    west: float,
    south: float,
    east: float,
    north: float,
) -> int:
    """Dynamic timeout for Earth Engine thumbnail fetch.

    Scales with image dimensions and ROI area (degree²,
    cosine-corrected for latitude).
    """
    mid_lat = math.radians((south + north) / 2)
    area_deg2 = (
        abs(east - west)
        * math.cos(mid_lat)
        * abs(north - south)
    )
    dim_factor = dimensions / 512
    area_factor = max(1.0, area_deg2 / 25)
    timeout = int(120 * dim_factor * area_factor)
    return min(timeout, 600)


def _scale_controls(
    data_key: str,
    source: str,
    hp: dict,
) -> tuple[float | None, float | None]:
    """Render scale-adjustment controls.

    Returns ``(vis_min, vis_max)`` — both *None*
    when the user keeps the default scale.
    """
    cfg = get_config(data_key, source)
    scale = cfg.display_scale
    unit = cfg.display_unit

    min_key = "vis_min"
    max_key = "vis_max"

    # Reset sliders when the variable or source changes.
    _scale_id = f"{data_key}|{source}"
    if st.session_state.get("_scale_var_id") != _scale_id:
        st.session_state["_scale_var_id"] = _scale_id
        st.session_state[min_key] = (
            cfg.vis_min * scale
        )
        st.session_state[max_key] = (
            cfg.vis_max * scale
        )
        st.session_state.pop("auto_scale", None)
        st.session_state.pop("_prev_auto_scale", None)

    # Initialise slider session state on first run.
    if min_key not in st.session_state:
        st.session_state[min_key] = (
            cfg.vis_min * scale
        )
    if max_key not in st.session_state:
        st.session_state[max_key] = (
            cfg.vis_max * scale
        )

    is_rgb = getattr(cfg, "is_rgb", False)

    with st.expander("Scale settings"):
        auto = st.checkbox(
            "Auto-compute from data",
            key="auto_scale",
            disabled=is_rgb,
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


def _get_variable_caption(data_key: str) -> str | None:
    """Return explanatory caption for special variables."""
    if data_key == "CH4_ANOMALY":
        return (
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
        )
    if data_key == "MBSP":
        return (
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
        )
    if data_key == "B12_B11":
        return (
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
        )
    if data_key == "VV_VH_RATIO":
        return (
            "**Reading the VV/VH ratio scale:** "
            "This shows the difference VV \u2212 VH "
            "in dB, equivalent to the log of the "
            "linear power ratio VV\u2097\u1d35\u2099 / "
            "VH\u2097\u1d35\u2099. "
            "**High values (red, 10\u201315 dB)** "
            "indicate VV dominates \u2014 typical of "
            "calm water, bare soil, or urban "
            "structures with strong specular or "
            "double-bounce returns. "
            "**Mid-range values (5\u201310 dB)** are "
            "typical of cropland and mixed "
            "land cover. "
            "**Low values (blue, near 0 dB)** "
            "indicate strong depolarisation "
            "\u2014 typical of dense vegetation "
            "with significant volume scattering. "
            "Useful for land-cover discrimination "
            "independent of absolute backscatter "
            "intensity."
        )
    if data_key == "RVI":
        return (
            "**Reading the Radar Vegetation Index:** "
            "RVI = 4 \u00b7 VH\u2097\u1d35\u2099 / "
            "(VV\u2097\u1d35\u2099 + VH\u2097\u1d35\u2099), "
            "where linear power is derived from the "
            "dB backscatter. "
            "**Values near 0** indicate bare soil, "
            "open water, or built surfaces where "
            "VH cross-polarisation is weak. "
            "**Values near 1** indicate dense "
            "vegetation canopies that strongly "
            "depolarise the radar signal. "
            "Unlike optical vegetation indices, "
            "RVI is unaffected by clouds or smoke "
            "and works in all weather conditions."
        )
    if data_key == "NDVI":
        return (
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
        )
    if data_key == "NDWI":
        return (
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
        )
    if data_key == "EVI":
        return (
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
        )
    if data_key == "VV":
        return (
            "**Reading the VV backscatter scale:** "
            "VV co-polarized radar backscatter "
            "in dB. "
            "**High values (bright)** near 0 dB "
            "indicate strong returns from urban "
            "areas, rough water, or steep terrain. "
            "**Mid-range values** (\u221215 to "
            "\u22125 dB) are typical of vegetated "
            "land and cropland. "
            "**Low values (dark)** below \u221220 dB "
            "indicate calm water, smooth surfaces, "
            "or radar shadow."
        )
    if data_key == "VH":
        return (
            "**Reading the VH backscatter scale:** "
            "VH cross-polarized radar backscatter "
            "in dB. "
            "**Higher values (brighter)** indicate "
            "strong volume scattering from dense "
            "vegetation canopies or rough terrain. "
            "**Mid-range values** (\u221220 to "
            "\u221210 dB) are typical of crops and "
            "mixed land cover. "
            "**Low values (dark)** below \u221225 dB "
            "indicate smooth surfaces such as "
            "calm water, bare soil, or urban areas "
            "with minimal cross-pol return."
        )
    if data_key.startswith("B") and data_key.lstrip("B").replace("A", "").isdigit():
        cfg = get_config(data_key, "s2")
        return (
            f"**Reading the {cfg.name.split(' \u2014 ')[0]} "
            f"scale:** "
            f"{cfg.name} measures surface "
            "reflectance. Higher values (brighter) "
            "indicate stronger reflection at this "
            "wavelength; lower values (darker) "
            "indicate absorption or low return."
        )
    return None


def _render_methane_map(
    hp: dict,
    authenticate_on_fail: bool,
) -> None:
    """Render the spatial map in methane detection mode.

    Handles multi-source layers (S5P + S2) with optional
    vegetation/water masking.
    """
    data_keys = hp.get("data_keys", [hp["data_key"]])

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

    # Masking params from heatmap_params.
    mask_veg = hp.get("methane_mask_vegetation", True)
    mask_water = hp.get("methane_mask_water", True)
    ndvi_thresh = hp.get("methane_ndvi_threshold", 0.3)
    ndwi_thresh = hp.get("methane_ndwi_threshold", 0.0)

    has_ch4_anomaly = "CH4_ANOMALY" in data_keys
    show_rgb = hp.get("methane_show_rgb", False)
    show_s1 = hp.get("methane_show_s1", False)
    s1_variable = hp.get("methane_s1_variable", "VV")
    show_wind = hp.get("show_wind", False)
    show_classification = hp.get(
        "methane_show_classification", False,
    )

    # ── Mode toggle ──────────────────────────────────
    if has_ch4_anomaly and len(data_keys) == 1:
        mode = "Anomaly"
        st.info(
            "Methane anomaly mode: the date range "
            "is used as the baseline reference. "
            "Select a target date to compare "
            "against it."
        )
    else:
        mode = st.radio(
            "Composite type",
            options=[
                "Date composite",
                "Mean composite",
            ],
            horizontal=True,
            key="heatmap_mode",
        )

    # ── Date controls ────────────────────────────────
    selected_date = None
    half_window = 0
    window_label = ""

    from datetime import timedelta as _td

    _start = date.fromisoformat(hp["start_date"])
    _end = date.fromisoformat(hp["end_date"])
    available_dates = [
        _start + _td(days=i)
        for i in range((_end - _start).days)
    ]

    if mode in ("Date composite", "Anomaly"):
        if len(available_dates) < 2:
            st.info(
                "Need at least 2 dates to "
                "use the date composite."
            )
            return

        selected_date = cast(
            date,
            st.select_slider(
                "Select target date"
                if mode == "Anomaly"
                else "Select date",
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
        if mode == "Anomaly":
            st.caption(
                f"Target: {window_label} — "
                f"Reference: {hp['start_date']} "
                f"to {hp['end_date']}"
            )
        else:
            st.caption(f"Showing: {window_label}")
    else:
        st.caption(
            f"Composite mean from "
            f"{hp['start_date']} to "
            f"{hp['end_date']}"
        )

    # ── Temporal animation ─────────────────────────────
    if mode in ("Date composite", "Anomaly"):
        with st.expander("Temporal Animation"):
            anim_enabled = st.checkbox(
                "Enable date stepping",
                value=False,
                key="methane_anim_enabled",
            )
            if anim_enabled and len(available_dates) >= 2:
                step_days = st.slider(
                    "Step size (days)",
                    min_value=1,
                    max_value=14,
                    value=7,
                    key="methane_anim_step",
                )
                anim_dates = available_dates[::step_days]
                if len(anim_dates) < 2:
                    st.warning(
                        "Not enough dates for "
                        "animation with this "
                        "step size."
                    )
                else:
                    anim_idx = st.slider(
                        "Animation frame",
                        min_value=0,
                        max_value=len(anim_dates) - 1,
                        value=0,
                        key="methane_anim_idx",
                    )
                    def _anim_prev():
                        st.session_state[
                            "methane_anim_idx"
                        ] -= 1

                    def _anim_next():
                        st.session_state[
                            "methane_anim_idx"
                        ] += 1

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.button(
                            "\u25c0 Previous",
                            key="anim_prev",
                            disabled=anim_idx == 0,
                            on_click=_anim_prev,
                        )
                    with col2:
                        st.caption(
                            f"Frame {anim_idx + 1}/"
                            f"{len(anim_dates)}: "
                            f"{anim_dates[anim_idx]}"
                        )
                    with col3:
                        st.button(
                            "Next \u25b6",
                            key="anim_next",
                            disabled=(
                                anim_idx
                                == len(anim_dates) - 1
                            ),
                            on_click=_anim_next,
                        )

                    # Override selected_date.
                    selected_date = anim_dates[anim_idx]
                    window_label = (
                        f"{selected_date}"
                        if half_window == 0
                        else (
                            f"{selected_date} "
                            f"+/- {half_window} days"
                        )
                    )

                    # Thumbnail date strip.
                    preview_dates = anim_dates[:8]
                    st.caption("Quick preview:")
                    thumb_cols = st.columns(
                        min(len(preview_dates), 8),
                    )
                    for i, d in enumerate(preview_dates):
                        with thumb_cols[i]:
                            is_current = (
                                d == selected_date
                            )
                            label = (
                                f"**{d}**"
                                if is_current
                                else str(d)
                            )
                            st.caption(label)

    # ── Layer settings (opacity per variable) ────────
    opacities: dict[str, float] = {}
    show_opacity_expander = (
        len(data_keys) > 1
        or show_rgb
        or show_s1
        or show_classification
    )
    if show_opacity_expander:
        with st.expander("Layer settings"):
            for dk in data_keys:
                src = _resolve_source(dk, "methane")
                label = (
                    f"{dk} (S5P)" if src == "s5p"
                    else f"{dk} (S2)"
                )
                opacities[dk] = st.slider(
                    f"{label} opacity",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.75,
                    step=0.05,
                    key=f"opacity_{dk}",
                )
            if show_rgb:
                opacities["RGB"] = st.slider(
                    "RGB (S2) opacity",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.6,
                    step=0.05,
                    key="opacity_RGB",
                )
            if show_s1:
                opacities["S1"] = st.slider(
                    f"S1 {s1_variable} opacity",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.5,
                    step=0.05,
                    key="opacity_S1",
                )
            if show_classification:
                opacities["classification"] = st.slider(
                    "Source classification opacity",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.6,
                    step=0.05,
                    key="opacity_classification",
                )
    else:
        opacities[data_keys[0]] = 0.75

    # ── Build tile layers ────────────────────────────
    layer_specs: list[LayerSpec] = []
    _anomaly_vis: tuple[float, float] | None = None
    try:
        for dk in data_keys:
            src = _resolve_source(dk, "methane")

            if dk == "CH4_ANOMALY":
                if selected_date is None:
                    st.caption(
                        "CH\u2084 anomaly requires a "
                        "target date — skipped in "
                        "mean composite mode."
                    )
                    continue

                # Use cached scale if available;
                # auto-scale only on first load or
                # when user clicks recalculate.
                stored = st.session_state.get(
                    "_anomaly_scale",
                )
                force_auto = st.session_state.pop(
                    "_anomaly_recalc", False,
                )
                _mask_kw = dict(
                    mask_vegetation=mask_veg,
                    mask_water=mask_water,
                    ndvi_threshold=ndvi_thresh,
                    ndwi_threshold=ndwi_thresh,
                )
                if stored and not force_auto:
                    anom_vmin, anom_vmax = stored
                    with st.spinner(
                        f"Computing CH\u2084 anomaly "
                        f"for {window_label}..."
                    ):
                        tile_url, _, _ = (
                            cached_methane_anomaly_tile_url(
                                hp["west"],
                                hp["south"],
                                hp["east"],
                                hp["north"],
                                selected_date.isoformat(),
                                half_window,
                                hp["start_date"],
                                hp["end_date"],
                                vis_min=anom_vmin,
                                vis_max=anom_vmax,
                                auto_scale=False,
                                **_mask_kw,
                            )
                        )
                else:
                    with st.spinner(
                        f"Computing CH\u2084 anomaly "
                        f"for {window_label} "
                        f"(auto-scaling)..."
                    ):
                        tile_url, anom_vmin, anom_vmax = (
                            cached_methane_anomaly_tile_url(
                                hp["west"],
                                hp["south"],
                                hp["east"],
                                hp["north"],
                                selected_date.isoformat(),
                                half_window,
                                hp["start_date"],
                                hp["end_date"],
                                **_mask_kw,
                            )
                        )
                    st.session_state[
                        "_anomaly_scale"
                    ] = (anom_vmin, anom_vmax)

                _anomaly_vis = (anom_vmin, anom_vmax)
                layer_name = (
                    f"CH\u2084 anomaly {window_label}"
                )
            elif mode == "Date composite":
                with st.spinner(
                    f"Loading {dk} ({src}) for "
                    f"{window_label}..."
                ):
                    tile_url = (
                        cached_masked_date_tile_url(
                            dk,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            selected_date.isoformat(),
                            half_window,
                            source=src,
                            mask_vegetation=mask_veg,
                            mask_water=mask_water,
                            ndvi_threshold=ndvi_thresh,
                            ndwi_threshold=ndwi_thresh,
                        )
                    )
                layer_name = f"{dk} {window_label}"
            else:
                with st.spinner(
                    f"Loading mean {dk} ({src})..."
                ):
                    tile_url = cached_masked_tile_url(
                        dk,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                        source=src,
                        mask_vegetation=mask_veg,
                        mask_water=mask_water,
                        ndvi_threshold=ndvi_thresh,
                        ndwi_threshold=ndwi_thresh,
                    )
                layer_name = f"Mean {dk}"

            layer_specs.append(LayerSpec(
                tile_url=tile_url,
                layer_name=layer_name,
                source=src,
                opacity=opacities.get(dk, 0.75),
            ))

        # ── RGB reference layer ───────────────────────
        if show_rgb:
            with st.spinner("Loading RGB composite..."):
                if mode == "Date composite" and selected_date:
                    rgb_tile_url = cached_date_tile_url(
                        "RGB",
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        selected_date.isoformat(),
                        half_window,
                        source="s2",
                    )
                    rgb_layer_name = (
                        f"RGB {window_label}"
                    )
                else:
                    rgb_tile_url = cached_mean_tile_url(
                        "RGB",
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                        source="s2",
                    )
                    rgb_layer_name = "RGB (mean)"
            # Insert RGB at position 0 so it renders
            # below the methane layers.
            layer_specs.insert(
                0,
                LayerSpec(
                    tile_url=rgb_tile_url,
                    layer_name=rgb_layer_name,
                    source="s2",
                    opacity=opacities.get(
                        "RGB", 0.6
                    ),
                ),
            )

        # ── S1 SAR context layer ──────────────────────
        if show_s1:
            with st.spinner(
                f"Loading S1 {s1_variable} context layer..."
            ):
                if (
                    mode == "Date composite"
                    and selected_date
                ):
                    s1_tile_url = cached_date_tile_url(
                        s1_variable,
                        hp["west"], hp["south"],
                        hp["east"], hp["north"],
                        selected_date.isoformat(),
                        half_window,
                        source="s1",
                    )
                    s1_layer_name = (
                        f"S1 {s1_variable} "
                        f"{window_label}"
                    )
                else:
                    s1_tile_url = cached_mean_tile_url(
                        s1_variable,
                        hp["west"], hp["south"],
                        hp["east"], hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                        source="s1",
                    )
                    s1_layer_name = (
                        f"S1 {s1_variable} (mean)"
                    )
            layer_specs.insert(
                0,
                LayerSpec(
                    tile_url=s1_tile_url,
                    layer_name=s1_layer_name,
                    source="s1",
                    opacity=opacities.get("S1", 0.5),
                ),
            )

        # ── Source classification layer ───────────────
        if show_classification:
            with st.spinner(
                "Computing source classification..."
            ):
                try:
                    cls_tile_url = (
                        cached_source_classification_tile_url(
                            hp["west"], hp["south"],
                            hp["east"], hp["north"],
                            hp["start_date"],
                            hp["end_date"],
                            s1_vv_high=hp.get(
                                "methane_cls_s1_high",
                                -10.0,
                            ),
                            ndvi_veg=hp.get(
                                "methane_cls_ndvi_veg",
                                0.35,
                            ),
                            ndwi_water=hp.get(
                                "methane_cls_ndwi_water",
                                0.1,
                            ),
                            methane_signal=hp.get(
                                "methane_cls_methane_thresh",
                                -0.02,
                            ),
                        )
                    )
                    layer_specs.append(LayerSpec(
                        tile_url=cls_tile_url,
                        layer_name="Source Classification",
                        source="s2",
                        opacity=opacities.get(
                            "classification", 0.6,
                        ),
                    ))
                except Exception as exc:
                    st.warning(
                        f"Source classification failed: "
                        f"{exc}"
                    )

        base_map, fgs = (
            create_multilayer_heatmap_folium(
                layers=layer_specs,
                center_lat=center_lat,
                center_lon=center_lon,
                bounds=bounds,
            )
        )

        # ── ERA5 wind arrows ─────────────────────────
        if show_wind and selected_date:
            with st.spinner("Loading ERA5 wind data..."):
                try:
                    from openearth.providers.gee_era5 import (
                        sample_wind_grid,
                    )
                    from app.wind_overlay import (
                        add_wind_arrows,
                    )

                    roi = ee.Geometry.BBox(
                        hp["west"], hp["south"],
                        hp["east"], hp["north"],
                    )
                    wind_data = sample_wind_grid(
                        roi,
                        selected_date.isoformat(),
                        n_points=25,
                    )
                    wind_fg = add_wind_arrows(wind_data)
                    # Insert before ROI layer (last fg).
                    fgs.insert(-1, wind_fg)
                except Exception as exc:
                    st.warning(
                        f"Wind overlay failed: {exc}"
                    )

        st_folium(
            base_map,
            key="heatmap",
            feature_group_to_add=fgs,
            layer_control=folium.LayerControl(),
            height=500,
            width=None,
            returned_objects=[],
        )

    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not render heatmap.",
        )

    # ── Color legends ────────────────────────────────
    if show_rgb:
        render_color_legend("RGB", "s2")
    for dk in data_keys:
        src = _resolve_source(dk, "methane")
        if len(data_keys) > 1:
            st.caption(f"**{dk}**")
        if dk == "CH4_ANOMALY" and _anomaly_vis:
            render_color_legend(
                dk, src,
                vis_min=_anomaly_vis[0],
                vis_max=_anomaly_vis[1],
            )
            if st.button(
                "\u2699 Recalculate scale",
                key="anomaly_recalc_btn",
                help=(
                    "Re-run auto-scale on the "
                    "current date's anomaly image."
                ),
            ):
                st.session_state[
                    "_anomaly_recalc"
                ] = True
                st.session_state.pop(
                    "_anomaly_scale", None,
                )
                st.rerun()
        else:
            render_color_legend(dk, src)
        caption = _get_variable_caption(dk)
        if caption:
            st.caption(caption)

    if show_s1:
        render_color_legend(s1_variable, "s1")
    if show_classification:
        render_classification_legend()


def render(
    authenticate_on_fail: bool,
) -> None:
    if "heatmap_params" not in st.session_state:
        st.info("Run an analysis first.")
        return

    hp = st.session_state["heatmap_params"]

    # ── Methane mode early exit ───────────────────────
    if hp.get("methane_mode"):
        _render_methane_map(hp, authenticate_on_fail)
        return

    data_key = hp["data_key"]
    data_keys = hp.get("data_keys", [data_key])
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

    has_ch4_anomaly = "CH4_ANOMALY" in data_keys

    # ── Mode toggle ──────────────────────────────────
    if has_ch4_anomaly and len(data_keys) == 1:
        mode = "Anomaly"
        st.info(
            "Methane anomaly mode: the date range "
            "is used as the baseline reference. "
            "Select a target date to compare against it."
        )
    else:
        mode = st.radio(
            "Composite type",
            options=[
                "Date composite",
                "Mean composite",
            ],
            horizontal=True,
            key="heatmap_mode",
        )

    # ── Date controls (only for date / anomaly mode) ─
    selected_date = None
    half_window = 0
    window_label = ""

    from datetime import timedelta as _td

    _start = date.fromisoformat(hp["start_date"])
    _end = date.fromisoformat(hp["end_date"])
    available_dates = [
        _start + _td(days=i)
        for i in range((_end - _start).days)
    ]

    if mode in ("Date composite", "Anomaly"):
        if len(available_dates) < 2:
            st.info(
                "Need at least 2 dates to "
                "use the date composite."
            )
            return

        selected_date = cast(
            date,
            st.select_slider(
                "Select target date"
                if mode == "Anomaly"
                else "Select date",
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
        if mode == "Anomaly":
            st.caption(
                f"Target: {window_label} — "
                f"Reference: {hp['start_date']} "
                f"to {hp['end_date']}"
            )
        else:
            st.caption(f"Showing: {window_label}")
    else:
        st.caption(
            f"Composite mean of all {sat} passes "
            f"from {hp['start_date']} to "
            f"{hp['end_date']}"
        )

    # ── Scale controls (primary variable only) ───────
    vis_min, vis_max = _scale_controls(
        data_key, source, hp,
    )

    # ── Layer settings (opacity per variable) ────────
    opacities: dict[str, float] = {}
    if len(data_keys) > 1:
        with st.expander("Layer settings"):
            for dk in data_keys:
                opacities[dk] = st.slider(
                    f"{dk} opacity",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.75,
                    step=0.05,
                    key=f"opacity_{dk}",
                )
    else:
        opacities[data_keys[0]] = 0.75

    # ── Build tile layers ────────────────────────────
    layer_specs: list[LayerSpec] = []
    try:
        for dk in data_keys:
            # Primary variable uses custom scale;
            # others use registry defaults.
            dk_vis_min = (
                vis_min if dk == data_key else None
            )
            dk_vis_max = (
                vis_max if dk == data_key else None
            )

            if dk == "CH4_ANOMALY":
                if selected_date is None:
                    st.caption(
                        "CH\u2084 anomaly requires a "
                        "target date — skipped in "
                        "mean composite mode."
                    )
                    continue
                with st.spinner(
                    f"Computing CH\u2084 anomaly for "
                    f"{window_label}..."
                ):
                    tile_url = (
                        cached_methane_anomaly_tile_url(
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            selected_date.isoformat(),
                            half_window,
                            hp["start_date"],
                            hp["end_date"],
                            vis_min=dk_vis_min,
                            vis_max=dk_vis_max,
                        )
                    )
                layer_name = (
                    f"CH\u2084 anomaly {window_label}"
                )
            elif mode == "Date composite":
                with st.spinner(
                    f"Loading {dk} heatmap for "
                    f"{window_label}..."
                ):
                    tile_url = cached_date_tile_url(
                        dk,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        selected_date.isoformat(),
                        half_window,
                        source=source,
                        vis_min=dk_vis_min,
                        vis_max=dk_vis_max,
                    )
                layer_name = (
                    f"{dk} {window_label}"
                )
            else:
                with st.spinner(
                    f"Loading mean {dk} heatmap..."
                ):
                    tile_url = cached_mean_tile_url(
                        dk,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                        hp["start_date"],
                        hp["end_date"],
                        source=source,
                        vis_min=dk_vis_min,
                        vis_max=dk_vis_max,
                    )
                layer_name = f"Mean {dk}"

            layer_specs.append(LayerSpec(
                tile_url=tile_url,
                layer_name=layer_name,
                source=source,
                opacity=opacities.get(dk, 0.75),
            ))

        base_map, fgs = create_multilayer_heatmap_folium(
            layers=layer_specs,
            center_lat=center_lat,
            center_lon=center_lon,
            bounds=bounds,
        )

        # ── ERA5 wind arrows (explorer) ──────────────
        show_wind = hp.get("show_wind", False)
        if show_wind and selected_date:
            with st.spinner("Loading ERA5 wind data..."):
                try:
                    from openearth.providers.gee_era5 import (
                        sample_wind_grid,
                    )
                    from app.wind_overlay import (
                        add_wind_arrows,
                    )

                    roi = ee.Geometry.BBox(
                        hp["west"], hp["south"],
                        hp["east"], hp["north"],
                    )
                    wind_data = sample_wind_grid(
                        roi,
                        selected_date.isoformat(),
                        n_points=25,
                    )
                    wind_fg = add_wind_arrows(wind_data)
                    fgs.insert(-1, wind_fg)
                except Exception as exc:
                    st.warning(
                        f"Wind overlay failed: {exc}"
                    )

        st_folium(
            base_map,
            key="heatmap",
            feature_group_to_add=fgs,
            layer_control=folium.LayerControl(),
            height=500,
            width=None,
            returned_objects=[],
        )

    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not render heatmap.",
        )

    # ── Color legends (one per variable) ─────────────
    for dk in data_keys:
        dk_vis_min = (
            vis_min if dk == data_key else None
        )
        dk_vis_max = (
            vis_max if dk == data_key else None
        )
        if len(data_keys) > 1:
            st.caption(f"**{dk}**")
        render_color_legend(
            dk, source,
            vis_min=dk_vis_min,
            vis_max=dk_vis_max,
        )
        caption = _get_variable_caption(dk)
        if caption:
            st.caption(caption)

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
    _render_image_export(
        hp, data_key, source, available_dates,
    )


def _export_fingerprint(
    hp: dict,
    data_key: str,
    source: str,
    hm: dict,
) -> str:
    """Build a fingerprint to detect stale exports."""
    return (
        f"{data_key}|{source}"
        f"|{hm.get('mode')}"
        f"|{hm.get('selected_date')}"
        f"|{hm.get('half_window')}"
        f"|{hm.get('vis_min')}"
        f"|{hm.get('vis_max')}"
        f"|{hp.get('start_date')}"
        f"|{hp.get('end_date')}"
    )


def _render_image_export(
    hp: dict,
    data_key: str,
    source: str,
    available_dates: list[date] | None = None,
) -> None:
    """Render image export controls below the heatmap."""
    import io
    import itertools
    import traceback
    import urllib.request
    import zipfile

    from app.analysis import (
        cached_thumb_url,
        cached_date_thumb_url,
        cached_download_url,
    )
    from app.config import TRACE_GASES, S2_INDICES, S1_VARIABLES
    from app.errors import show_image_error

    hm = st.session_state.get("current_heatmap", {})
    mode = hm.get("mode", "Mean composite")

    # Clear stale exports when heatmap params change.
    fp = _export_fingerprint(hp, data_key, source, hm)
    if st.session_state.get("_export_fp") != fp:
        st.session_state["_export_fp"] = fp
        st.session_state.pop("export_img_bytes", None)
        st.session_state.pop("export_img_meta", None)
        st.session_state.pop("export_tiff_url", None)
        st.session_state.pop("batch_results", None)

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
                        _timeout = _ee_fetch_timeout(
                            dimensions,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                        )
                        with urllib.request.urlopen(
                            thumb_url, timeout=_timeout,
                        ) as resp:
                            img_bytes = resp.read()

                    fname = (
                        f"openearth_{data_key}"
                        f"_{hp['start_date']}"
                        f"_{hp['end_date']}"
                        f".{ext}"
                    )
                    st.session_state[
                        "export_img_bytes"
                    ] = img_bytes
                    st.session_state[
                        "export_img_meta"
                    ] = {
                        "format": img_type,
                        "mime": mime,
                        "fname": fname,
                        "caption": (
                            f"{data_key} composite"
                        ),
                    }
                except Exception as exc:
                    show_image_error(
                        exc,
                        "Could not generate image.",
                    )

            # Show persisted image outside button block.
            stored = st.session_state.get(
                "export_img_bytes",
            )
            meta = st.session_state.get(
                "export_img_meta",
            )
            if stored and meta:
                st.image(
                    stored,
                    caption=meta["caption"],
                )
                st.download_button(
                    label=(
                        f"Download {meta['format']}"
                    ),
                    data=stored,
                    file_name=meta["fname"],
                    mime=meta["mime"],
                    key="dl_export",
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
                    st.session_state[
                        "export_tiff_url"
                    ] = dl_url
                except Exception as exc:
                    show_image_error(
                        exc,
                        "Could not generate GeoTIFF.",
                    )

            # Show persisted GeoTIFF link.
            stored_tiff = st.session_state.get(
                "export_tiff_url",
            )
            if stored_tiff:
                st.markdown(
                    f"[Download GeoTIFF]"
                    f"({stored_tiff})"
                )

        # ── Batch Export ──────────────────────────────
        st.divider()
        st.subheader("Batch Export")

        if source == "s2":
            variables = S2_INDICES
        elif source == "s1":
            variables = S1_VARIABLES
        else:
            variables = TRACE_GASES

        batch_mode = st.radio(
            "Mode",
            ["Single dates", "Period averages"],
            horizontal=True,
            key="batch_mode",
        )

        if batch_mode == "Single dates":
            batch_dates = st.multiselect(
                "Dates",
                options=available_dates or [],
                default=[],
                key="batch_dates",
                help="Select dates to export.",
            )

        else:
            _PERIOD_OPTIONS = {
                "1 month": 1,
                "2 months": 2,
                "3 months": 3,
                "6 months": 6,
                "1 year": 12,
            }
            period_label = st.selectbox(
                "Period length",
                options=list(_PERIOD_OPTIONS.keys()),
                key="batch_period_length",
            )
            period_months = _PERIOD_OPTIONS[period_label]

            from datetime import date as _date

            sidebar_start = _date.fromisoformat(
                hp["start_date"],
            )
            sidebar_end = _date.fromisoformat(
                hp["end_date"],
            )
            pcol1, pcol2 = st.columns(2)
            with pcol1:
                range_start = st.date_input(
                    "Range start",
                    value=sidebar_start,
                    key="batch_range_start",
                )
            with pcol2:
                range_end = st.date_input(
                    "Range end",
                    value=sidebar_end,
                    key="batch_range_end",
                )

            def _period_windows(start, end, months):
                """Generate (start, end) for each period."""
                windows = []
                cur = start
                while cur < end:
                    y = cur.year + (
                        (cur.month - 1 + months) // 12
                    )
                    m = (cur.month - 1 + months) % 12 + 1
                    nxt = cur.replace(
                        year=y, month=m, day=1,
                    )
                    if nxt > end:
                        nxt = end
                    windows.append((cur, nxt))
                    cur = nxt
                return windows

            period_windows = _period_windows(
                range_start, range_end, period_months,
            )

        batch_vars = st.multiselect(
            "Variables",
            options=list(variables.keys()),
            format_func=lambda k: variables[k],
            default=[data_key],
            key="batch_vars",
            help="Select variables to export.",
        )

        batch_fmt = st.selectbox(
            "Batch format",
            options=["PNG", "JPEG"],
            key="batch_format",
        )
        batch_dims = st.slider(
            "Batch image size (longest edge, px)",
            min_value=256,
            max_value=4096,
            value=1024,
            step=256,
            key="batch_dimensions",
        )

        b_fmt_map = {"PNG": "png", "JPEG": "jpg"}
        b_fmt = b_fmt_map[batch_fmt]
        b_mime = (
            "image/png" if b_fmt == "png"
            else "image/jpeg"
        )
        b_ext = "png" if b_fmt == "png" else "jpeg"

        _MAX_BATCH = 20

        if batch_mode == "Single dates":
            combos = list(
                itertools.product(
                    batch_dates, batch_vars,
                ),
            )
            if len(combos) > _MAX_BATCH:
                st.warning(
                    f"Batch limited to {_MAX_BATCH} "
                    f"combinations "
                    f"(selected {len(combos)}). "
                    "Reduce dates or variables."
                )
                combos = combos[:_MAX_BATCH]
        else:
            combos = list(
                itertools.product(
                    period_windows, batch_vars,
                ),
            )
            if len(combos) > _MAX_BATCH:
                st.warning(
                    f"Batch limited to {_MAX_BATCH} "
                    f"combinations "
                    f"(selected {len(combos)}). "
                    "Reduce period range or variables."
                )
                combos = combos[:_MAX_BATCH]

        can_generate = len(combos) > 0
        if can_generate:
            if batch_mode == "Period averages":
                n_periods = len(period_windows)
                st.caption(
                    f"{n_periods} period(s) x "
                    f"{len(batch_vars)} variable(s) = "
                    f"{len(combos)} image(s) "
                    "will be generated."
                )
            else:
                st.caption(
                    f"{len(combos)} image(s) will be "
                    "generated."
                )

        if st.button(
            "Generate Batch",
            key="gen_batch",
            disabled=not can_generate,
        ):
            results: list[dict] = []
            progress = st.progress(
                0,
                text=(
                    f"Generating batch "
                    f"(0/{len(combos)})..."
                ),
            )
            for i, combo in enumerate(combos):
                if batch_mode == "Single dates":
                    d, var = combo
                    label = str(d)
                    progress.progress(
                        i / len(combos),
                        text=(
                            f"Generating {var} @ "
                            f"{label} "
                            f"({i + 1}/{len(combos)})..."
                        ),
                    )
                    try:
                        thumb_url = (
                            cached_date_thumb_url(
                                var,
                                hp["west"],
                                hp["south"],
                                hp["east"],
                                hp["north"],
                                d.isoformat(),
                                hm.get(
                                    "half_window", 7,
                                ),
                                source=source,
                                vis_min=(
                                    hm.get("vis_min")
                                    if var == data_key
                                    else None
                                ),
                                vis_max=(
                                    hm.get("vis_max")
                                    if var == data_key
                                    else None
                                ),
                                dimensions=batch_dims,
                                img_format=b_fmt,
                            )
                        )
                        fname = (
                            f"openearth_{var}"
                            f"_{d}.{b_ext}"
                        )
                    except Exception as exc:
                        results.append({
                            "date": label,
                            "var": var,
                            "bytes": None,
                            "fname": None,
                            "error": str(exc),
                            "traceback": (
                                traceback.format_exc()
                            ),
                        })
                        progress.progress(
                            (i + 1) / len(combos),
                            text=(
                                f"Failed {var} @ "
                                f"{label} "
                                f"({i + 1}/{len(combos)})"
                            ),
                        )
                        continue
                else:
                    (p_start, p_end), var = combo
                    label = (
                        f"{p_start.isoformat()} to "
                        f"{p_end.isoformat()}"
                    )
                    progress.progress(
                        i / len(combos),
                        text=(
                            f"Generating {var} "
                            f"mean {label} "
                            f"({i + 1}/{len(combos)})..."
                        ),
                    )
                    try:
                        thumb_url = cached_thumb_url(
                            var,
                            hp["west"],
                            hp["south"],
                            hp["east"],
                            hp["north"],
                            p_start.isoformat(),
                            p_end.isoformat(),
                            source=source,
                            vis_min=(
                                hm.get("vis_min")
                                if var == data_key
                                else None
                            ),
                            vis_max=(
                                hm.get("vis_max")
                                if var == data_key
                                else None
                            ),
                            dimensions=batch_dims,
                            img_format=b_fmt,
                        )
                        fname = (
                            f"openearth_{var}"
                            f"_{p_start}_{p_end}"
                            f".{b_ext}"
                        )
                    except Exception as exc:
                        results.append({
                            "date": label,
                            "var": var,
                            "bytes": None,
                            "fname": None,
                            "error": str(exc),
                            "traceback": (
                                traceback.format_exc()
                            ),
                        })
                        progress.progress(
                            (i + 1) / len(combos),
                            text=(
                                f"Failed {var} @ "
                                f"{label} "
                                f"({i + 1}/{len(combos)})"
                            ),
                        )
                        continue

                try:
                    _timeout = _ee_fetch_timeout(
                        batch_dims,
                        hp["west"],
                        hp["south"],
                        hp["east"],
                        hp["north"],
                    )
                    with urllib.request.urlopen(
                        thumb_url, timeout=_timeout,
                    ) as resp:
                        img_bytes = resp.read()
                    results.append({
                        "date": label,
                        "var": var,
                        "bytes": img_bytes,
                        "fname": fname,
                        "error": None,
                    })
                except Exception as exc:
                    results.append({
                        "date": label,
                        "var": var,
                        "bytes": None,
                        "fname": None,
                        "error": str(exc),
                        "traceback": (
                            traceback.format_exc()
                        ),
                    })
                progress.progress(
                    (i + 1) / len(combos),
                    text=(
                        f"Done {var} @ {label} "
                        f"({i + 1}/{len(combos)})"
                    ),
                )
            progress.empty()
            st.session_state["batch_results"] = results

        # Show persisted batch results.
        batch = st.session_state.get("batch_results")
        if batch:
            ok = [r for r in batch if r["bytes"]]
            fail = [r for r in batch if r["error"]]

            if fail:
                st.warning(
                    f"{len(fail)} image(s) failed.",
                )
                for r in fail:
                    st.caption(
                        f"{r['var']} @ {r['date']}: "
                        f"{r['error']}"
                    )
                    tb = r.get("traceback")
                    if tb:
                        with st.expander(
                            "Error details",
                            expanded=False,
                        ):
                            st.code(
                                tb, language="text",
                            )

            for r in ok:
                st.image(
                    r["bytes"],
                    caption=(
                        f"{r['var']} — {r['date']}"
                    ),
                )
                st.download_button(
                    label=f"Download {r['fname']}",
                    data=r["bytes"],
                    file_name=r["fname"],
                    mime=b_mime,
                    key=(
                        f"dl_batch"
                        f"_{r['var']}_{r['date']}"
                    ),
                )

            if len(ok) > 1:
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(
                    zip_buf, "w",
                    zipfile.ZIP_DEFLATED,
                ) as zf:
                    for r in ok:
                        zf.writestr(
                            r["fname"], r["bytes"],
                        )
                zip_buf.seek(0)
                st.download_button(
                    "Download All (ZIP)",
                    data=zip_buf.getvalue(),
                    file_name=(
                        f"openearth_batch"
                        f"_{hp['start_date']}"
                        f"_{hp['end_date']}"
                        f".zip"
                    ),
                    mime="application/zip",
                    key="dl_batch_zip",
                )
