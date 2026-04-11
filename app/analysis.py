"""Cached tile helpers and analysis orchestration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import ee
import pandas as pd
import streamlit as st

from openearth.analytics.daily_timeseries import (
    build_daily_timeseries,
    BATCH_SIZE,
)
from openearth.providers import get_config
from openearth.providers.gee_session import initialize_ee
from openearth.visualization.heatmap import (
    build_mean_composite,
    build_date_composite,
    build_methane_anomaly_composite,
    compute_anomaly_vis_range,
    compute_vis_range,
    get_tile_url,
    get_thumb_url,
    get_download_url,
    get_vis_params,
)

from app.config import SidebarConfig
from app.errors import show_ee_error


# ── Color legend ──────────────────────────────────────────────


def render_color_legend(
    data_key: str,
    source: str = "s5p",
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> None:
    """Render an HTML color bar legend."""
    cfg = get_config(data_key, source)
    if getattr(cfg, "is_rgb", False):
        st.caption("True colour composite (B4 / B3 / B2)")
        return
    gradient_css = ", ".join(cfg.palette)
    raw_min = vis_min if vis_min is not None else cfg.vis_min
    raw_max = vis_max if vis_max is not None else cfg.vis_max
    label_min = raw_min * cfg.display_scale
    label_max = raw_max * cfg.display_scale
    unit = cfg.display_unit

    # Use fewer significant figures for small-magnitude
    # values (e.g. anomaly deltas) to keep labels tidy.
    fmt = ".2g" if abs(label_max - label_min) < 1 else ".4g"

    st.markdown(
        f"""
        <div style="display:flex;align-items:center;
                    margin:8px 0 16px 0;">
            <span style="font-size:0.85em;
                         margin-right:8px;">
                {label_min:{fmt}} {unit}
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
                {label_max:{fmt}} {unit}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Cached tile helpers ──────────────────────────────────────


@st.cache_data(ttl=3600, show_spinner=False)
def cached_mean_tile_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
    source: str = "s5p",
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> str:
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_mean_composite(
        data_key, roi, start_date, end_date,
        source=source,
    )
    return get_tile_url(
        image, data_key, source,
        vis_min=vis_min, vis_max=vis_max,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_date_tile_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    target_date: str,
    half_window_days: int,
    source: str = "s5p",
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> str:
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_date_composite(
        data_key, roi, target_date,
        half_window_days,
        source=source,
    )
    return get_tile_url(
        image, data_key, source,
        vis_min=vis_min, vis_max=vis_max,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_methane_anomaly_tile_url(
    west: float, south: float,
    east: float, north: float,
    target_date: str,
    half_window_days: int,
    ref_start: str, ref_end: str,
    vis_min: float | None = None,
    vis_max: float | None = None,
    auto_scale: bool = True,
    mask_vegetation: bool = False,
    mask_water: bool = False,
    ndvi_threshold: float = 0.3,
    ndwi_threshold: float = 0.0,
) -> tuple[str, float, float]:
    """Return (tile_url, vis_min, vis_max) for CH4 anomaly.

    When *auto_scale* is True (default) the colour ramp is
    centred on the image median so that the uniform background
    appears neutral and only local deviations stand out.
    """
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_methane_anomaly_composite(
        roi, target_date, half_window_days,
        ref_start, ref_end,
    )
    if mask_vegetation or mask_water:
        from datetime import date as _date

        from openearth.masking.vegetation_water import (
            apply_vegetation_water_mask,
        )

        td = _date.fromisoformat(target_date)
        mask_start = (
            td - timedelta(days=max(half_window_days, 30))
        ).isoformat()
        mask_end = (
            td + timedelta(days=max(half_window_days, 30) + 1)
        ).isoformat()
        image = apply_vegetation_water_mask(
            image, roi, mask_start, mask_end,
            mask_vegetation=mask_vegetation,
            mask_water=mask_water,
            ndvi_threshold=ndvi_threshold,
            ndwi_threshold=ndwi_threshold,
        )
    if auto_scale and vis_min is None and vis_max is None:
        vis_min, vis_max = compute_anomaly_vis_range(
            image, geometry=roi,
        )
    params = get_vis_params(
        "CH4_ANOMALY", "s2",
        vis_min=vis_min, vis_max=vis_max,
    )
    map_id_dict = image.getMapId(params)
    return (
        map_id_dict["tile_fetcher"].url_format,
        float(vis_min) if vis_min is not None
        else params["min"],
        float(vis_max) if vis_max is not None
        else params["max"],
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_masked_tile_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
    source: str = "s2",
    mask_vegetation: bool = False,
    mask_water: bool = False,
    ndvi_threshold: float = 0.3,
    ndwi_threshold: float = 0.0,
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> str:
    """Build a mean composite with optional veg/water masking."""
    from openearth.masking.vegetation_water import (
        apply_vegetation_water_mask,
    )

    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_mean_composite(
        data_key, roi, start_date, end_date,
        source=source,
    )
    if mask_vegetation or mask_water:
        image = apply_vegetation_water_mask(
            image, roi, start_date, end_date,
            mask_vegetation=mask_vegetation,
            mask_water=mask_water,
            ndvi_threshold=ndvi_threshold,
            ndwi_threshold=ndwi_threshold,
        )
    return get_tile_url(
        image, data_key, source,
        vis_min=vis_min, vis_max=vis_max,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_masked_date_tile_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    target_date: str,
    half_window_days: int,
    source: str = "s2",
    mask_vegetation: bool = False,
    mask_water: bool = False,
    ndvi_threshold: float = 0.3,
    ndwi_threshold: float = 0.0,
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> str:
    """Build a date composite with optional veg/water masking."""
    from openearth.masking.vegetation_water import (
        apply_vegetation_water_mask,
    )

    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_date_composite(
        data_key, roi, target_date,
        half_window_days,
        source=source,
    )
    if mask_vegetation or mask_water:
        from datetime import date as _date

        td = _date.fromisoformat(target_date)
        mask_start = (
            td - timedelta(days=max(half_window_days, 30))
        ).isoformat()
        mask_end = (
            td + timedelta(days=max(half_window_days, 30) + 1)
        ).isoformat()
        image = apply_vegetation_water_mask(
            image, roi, mask_start, mask_end,
            mask_vegetation=mask_vegetation,
            mask_water=mask_water,
            ndvi_threshold=ndvi_threshold,
            ndwi_threshold=ndwi_threshold,
        )
    return get_tile_url(
        image, data_key, source,
        vis_min=vis_min, vis_max=vis_max,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_vis_range(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
    source: str = "s5p",
) -> tuple[float, float]:
    """Compute percentile-based vis range for the mean composite."""
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_mean_composite(
        data_key, roi, start_date, end_date,
        source=source,
    )
    return compute_vis_range(
        image, data_key, source,
        geometry=roi,
    )


# ── Source classification ────────────────────────────────────


@st.cache_data(ttl=3600, show_spinner=False)
def cached_source_classification_tile_url(
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
    s1_vv_high: float = -10.0,
    ndvi_veg: float = 0.35,
    ndwi_water: float = 0.1,
    methane_signal: float = -0.02,
) -> str:
    """Return a tile URL for methane source classification."""
    from openearth.analytics.source_classification import (
        ClassificationThresholds,
        classify_methane_sources,
        CLASS_PALETTE,
    )

    roi = ee.Geometry.BBox(west, south, east, north)
    thresholds = ClassificationThresholds(
        s1_vv_high=s1_vv_high,
        ndvi_veg=ndvi_veg,
        ndwi_water=ndwi_water,
        methane_signal=methane_signal,
    )
    image = classify_methane_sources(
        roi, start_date, end_date,
        thresholds=thresholds,
    )
    params = {
        "min": 1,
        "max": 5,
        "palette": CLASS_PALETTE,
    }
    map_id_dict = image.getMapId(params)
    return map_id_dict["tile_fetcher"].url_format


def render_classification_legend() -> None:
    """Render a categorical legend for source classification."""
    from openearth.analytics.source_classification import (
        CLASS_LABELS,
        CLASS_PALETTE,
    )

    items_html = ""
    for idx, (_, label) in enumerate(CLASS_LABELS.items()):
        color = CLASS_PALETTE[idx]
        items_html += (
            f'<span style="display:inline-flex;'
            f"align-items:center;"
            f'margin-right:12px;">'
            f'<span style="width:14px;height:14px;'
            f"background:{color};border-radius:2px;"
            f"margin-right:4px;"
            f'display:inline-block;"></span>'
            f"{label}</span>"
        )
    st.markdown(
        f'<div style="margin:8px 0 16px 0;'
        f'font-size:0.85em;">{items_html}</div>',
        unsafe_allow_html=True,
    )


# ── Image export helpers ─────────────────────────────────────


@st.cache_data(ttl=3600, show_spinner=False)
def cached_thumb_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
    source: str = "s5p",
    vis_min: float | None = None,
    vis_max: float | None = None,
    dimensions: int = 1024,
    img_format: str = "png",
) -> str:
    """Return a thumbnail URL for the mean composite."""
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_mean_composite(
        data_key, roi, start_date, end_date,
        source=source,
    )
    return get_thumb_url(
        image, data_key, roi, source,
        vis_min=vis_min, vis_max=vis_max,
        dimensions=dimensions,
        img_format=img_format,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_date_thumb_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    target_date: str,
    half_window_days: int,
    source: str = "s5p",
    vis_min: float | None = None,
    vis_max: float | None = None,
    dimensions: int = 1024,
    img_format: str = "png",
) -> str:
    """Return a thumbnail URL for a date composite."""
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_date_composite(
        data_key, roi, target_date,
        half_window_days,
        source=source,
    )
    return get_thumb_url(
        image, data_key, roi, source,
        vis_min=vis_min, vis_max=vis_max,
        dimensions=dimensions,
        img_format=img_format,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_download_url(
    data_key: str,
    west: float, south: float,
    east: float, north: float,
    start_date: str, end_date: str,
    source: str = "s5p",
) -> str:
    """Return a GeoTIFF download URL for the mean composite."""
    roi = ee.Geometry.BBox(west, south, east, north)
    image = build_mean_composite(
        data_key, roi, start_date, end_date,
        source=source,
    )
    return get_download_url(
        image, data_key, roi, source,
    )


# ── Heatmap param helpers ────────────────────────────────────


def heatmap_params(
    data_keys: list[str],
    start_date_iso: str,
    end_date_iso: str,
    west: float,
    south: float,
    east: float,
    north: float,
    project_id: str,
    source: str = "s5p",
) -> dict[str, Any]:
    return {
        "data_key": data_keys[0],
        "data_keys": list(data_keys),
        "start_date": start_date_iso,
        "end_date": end_date_iso,
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "project_id": project_id,
        "source": source,
    }


def _analysis_cache_key(
    data_key: str,
    start_date_iso: str,
    end_date_iso: str,
    west: float,
    south: float,
    east: float,
    north: float,
    project_id: str,
    source: str = "s5p",
) -> tuple[Any, ...]:
    return (
        source,
        data_key,
        start_date_iso,
        end_date_iso,
        round(west, 6),
        round(south, 6),
        round(east, 6),
        round(north, 6),
        project_id.strip(),
    )


# ── Session init (fast — no time series) ─────────────────────


def init_session(cfg: SidebarConfig) -> None:
    """Validate inputs, init EE, set heatmap_params.

    This is the fast path triggered by "Load Map".
    It does NOT build the daily time series.
    """
    if not cfg.project_id.strip():
        st.error("Project ID is required.")
        st.stop()
    if cfg.east <= cfg.west or cfg.north <= cfg.south:
        st.error(
            "Invalid bounding box. "
            "Ensure east > west and north > south."
        )
        st.stop()
    if cfg.end_date_inclusive < cfg.start_date:
        st.error(
            "End date must be on or after "
            "start date."
        )
        st.stop()

    end_date_exclusive = (
        cfg.end_date_inclusive + timedelta(days=1)
    )
    start_date_iso = cfg.start_date.isoformat()
    end_date_iso = end_date_exclusive.isoformat()

    with st.spinner("Initializing Earth Engine..."):
        try:
            initialize_ee(
                project_id=cfg.project_id,
                authenticate=cfg.authenticate_on_fail,
            )
        except ee.EEException as exc:
            show_ee_error(
                exc,
                "Could not initialize Earth Engine.",
            )
            st.stop()

    hp = heatmap_params(
        data_keys=cfg.selected_keys,
        start_date_iso=start_date_iso,
        end_date_iso=end_date_iso,
        west=cfg.west,
        south=cfg.south,
        east=cfg.east,
        north=cfg.north,
        project_id=cfg.project_id,
        source=cfg.source,
    )
    if cfg.mode == "methane":
        hp["methane_mode"] = True
        hp["methane_show_rgb"] = cfg.methane_show_rgb
        hp["methane_mask_vegetation"] = (
            cfg.methane_mask_vegetation
        )
        hp["methane_mask_water"] = cfg.methane_mask_water
        hp["methane_ndvi_threshold"] = (
            cfg.methane_ndvi_threshold
        )
        hp["methane_ndwi_threshold"] = (
            cfg.methane_ndwi_threshold
        )
        hp["methane_show_s1"] = cfg.methane_show_s1
        hp["methane_s1_variable"] = cfg.methane_s1_variable
        hp["methane_show_classification"] = (
            cfg.methane_show_classification
        )
        hp["methane_cls_s1_high"] = cfg.methane_cls_s1_high
        hp["methane_cls_ndvi_veg"] = cfg.methane_cls_ndvi_veg
        hp["methane_cls_ndwi_water"] = (
            cfg.methane_cls_ndwi_water
        )
        hp["methane_cls_methane_thresh"] = (
            cfg.methane_cls_methane_thresh
        )
    hp["show_wind"] = cfg.show_wind
    st.session_state["heatmap_params"] = hp
    # Clear stale time series and map view when params change.
    st.session_state.pop("analysis_df", None)
    st.session_state.pop("_map_view", None)
    st.session_state.pop("_anomaly_scale", None)


# ── Lazy time series loading ─────────────────────────────────


def ensure_timeseries(
    data_key: str | None = None,
    source: str | None = None,
) -> None:
    """Build the daily time series if not already cached.

    Reads parameters from ``st.session_state["heatmap_params"]``.
    Optional *data_key* and *source* override the stored values
    (used by methane mode to switch between variables).
    """
    hp = st.session_state.get("heatmap_params")
    if hp is None:
        st.error("Load the map first.")
        st.stop()

    if data_key is None:
        data_key = hp["data_key"]
    if source is None:
        source = hp.get("source", "s5p")
    start_date_iso = hp["start_date"]
    end_date_iso = hp["end_date"]

    cache_key = _analysis_cache_key(
        data_key=data_key,
        start_date_iso=start_date_iso,
        end_date_iso=end_date_iso,
        west=hp["west"],
        south=hp["south"],
        east=hp["east"],
        north=hp["north"],
        project_id=hp["project_id"],
        source=source,
    )
    analysis_cache = st.session_state.setdefault(
        "analysis_cache", {}
    )

    cached_entry = analysis_cache.get(cache_key)
    if isinstance(cached_entry, dict):
        cached_df = cached_entry.get("df")
        if isinstance(cached_df, pd.DataFrame):
            st.session_state["analysis_df"] = (
                cached_df.copy()
            )
            st.toast("Loaded cached time series")
            return

    data_cfg = get_config(data_key, source)

    try:
        initialize_ee(
            project_id=hp["project_id"],
        )
    except ee.EEException as exc:
        show_ee_error(
            exc,
            "Could not initialize Earth Engine.",
        )
        st.stop()

    roi = ee.Geometry.BBox(
        hp["west"], hp["south"],
        hp["east"], hp["north"],
    )

    progress_bar = st.progress(
        0,
        text=(
            f"Building {data_cfg.key} "
            "time series..."
        ),
    )

    def _on_progress(
        days_done: int, days_total: int,
    ) -> None:
        frac = (
            days_done / days_total
            if days_total
            else 1.0
        )
        progress_bar.progress(
            frac,
            text=(
                f"Processing {data_cfg.key} — "
                f"day {days_done}/{days_total}"
            ),
        )

    batch_sz = BATCH_SIZE
    try:
        while True:
            try:
                df = build_daily_timeseries(
                    data_key=data_key,
                    geometry=roi,
                    start_date=start_date_iso,
                    end_date=end_date_iso,
                    batch_size=batch_sz,
                    source=source,
                    progress_callback=_on_progress,
                )
            except ee.EEException as e:
                is_concurrent = (
                    "too many concurrent"
                    in str(e).lower()
                )
                if (
                    is_concurrent
                    and batch_sz >= 2
                ):
                    batch_sz = batch_sz // 2
                    st.toast(
                        "Reducing batch size "
                        f"to {batch_sz}..."
                    )
                    continue
                raise
            break
    except ee.EEException as exc:
        progress_bar.empty()
        show_ee_error(exc, "Analysis failed.")
        st.stop()

    progress_bar.empty()

    if df.empty:
        if source == "s2":
            st.info(
                "No clear-sky observations found. "
                "Sentinel-2 has a ~5-day revisit and "
                "cloud masking may remove all pixels. "
                "Try a longer date range or a "
                "larger ROI."
            )
        else:
            st.info(
                "No observations found for the "
                "selected variable, ROI, and "
                "date range."
            )
        return

    st.session_state["analysis_df"] = df

    analysis_cache[cache_key] = {
        "df": df.copy()
    }
    max_cache_entries = 5
    if len(analysis_cache) > max_cache_entries:
        oldest_key = next(iter(analysis_cache))
        analysis_cache.pop(oldest_key, None)
