"""Earth Engine error handling, cached tiles, and analysis orchestration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import ee
import pandas as pd
import streamlit as st

from openearth.analytics.trace_gas_daily import (
    build_daily_timeseries,
    BATCH_SIZE,
)
from openearth.providers.gas_registry import get_gas_config
from openearth.providers.gee_session import initialize_ee
from openearth.visualization.trace_gas_heatmap import (
    build_mean_composite,
    build_date_composite,
    get_tile_url,
)

from app.config import SidebarConfig


# ── EE error handling ─────────────────────────────────────────

# Phrases matched against lowercased EE error messages.
# Order matters: more specific phrases are checked first within
# each category, and auth is checked before quota to avoid
# ambiguity (e.g. "permission" should not match quota).

_AUTH_PHRASES = (
    "not authorized",
    "access denied",
    "permission denied",
    "authenticate",
    "credentials",
    "forbidden",
    " 401",
    " 403",
)

_QUOTA_PHRASES = (
    "too many concurrent",
    "quota exceeded",
    "rate limit",
    "limit exceeded",
    " 429",
    "user memory limit exceeded",
)

_TIMEOUT_PHRASES = (
    "timed out",
    "timeout",
    "deadline exceeded",
)

_EMPTY_PHRASES = (
    "collection is empty",
    "no images",
    "contains no images",
    "empty collection",
    "0 elements",
    "no valid pixels",
)


def classify_ee_error(
    exc: Exception,
) -> tuple[str, str]:
    """Classify an Earth Engine error by its message.

    Returns (category, user_message) where category is one of:
    "auth", "quota", "timeout", "empty", "unknown".
    """
    message = str(exc).lower()

    if any(p in message for p in _AUTH_PHRASES):
        return (
            "auth",
            "Earth Engine authentication or permissions failed. "
            "Check project access and sign in again.",
        )
    if any(p in message for p in _QUOTA_PHRASES):
        return (
            "quota",
            "Earth Engine quota or concurrency limit reached. "
            "Try a smaller ROI/date range or retry shortly.",
        )
    if any(p in message for p in _TIMEOUT_PHRASES):
        return (
            "timeout",
            "Earth Engine request timed out. "
            "Try a smaller ROI or date range.",
        )
    if any(p in message for p in _EMPTY_PHRASES):
        return (
            "empty",
            "No satellite observations are available for this "
            "gas, ROI, and time window.",
        )

    return (
        "unknown",
        "Unexpected Earth Engine error.",
    )


def show_ee_error(
    exc: Exception,
    context: str,
) -> None:
    """Display an EE error with the appropriate Streamlit severity."""
    # Let non-EE exceptions (TypeError, KeyError, etc.) propagate
    # so real bugs aren't silently swallowed.
    if not isinstance(exc, ee.EEException):
        raise exc

    category, user_message = classify_ee_error(exc)
    full_message = f"{context} {user_message}"

    if category == "auth":
        st.error(full_message)
    elif category == "quota":
        st.warning(full_message)
    elif category == "timeout":
        st.warning(full_message)
    elif category == "empty":
        st.info(full_message)
    else:
        st.error(full_message)

    with st.expander("Error details", expanded=False):
        st.exception(exc)


# ── Color legend ──────────────────────────────────────────────


def render_color_legend(gas_key: str) -> None:
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


# ── Cached tile helpers ──────────────────────────────────────


@st.cache_data(ttl=3600, show_spinner=False)
def cached_mean_tile_url(
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
def cached_date_tile_url(
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


# ── Heatmap param helpers ────────────────────────────────────


def heatmap_params(
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


# ── Run analysis ─────────────────────────────────────────────


def run_analysis(cfg: SidebarConfig) -> None:
    """Validate inputs, fetch data, and store results in session state."""
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

    cache_key = _analysis_cache_key(
        gas_key=cfg.selected_gas,
        start_date_iso=start_date_iso,
        end_date_iso=end_date_iso,
        west=cfg.west,
        south=cfg.south,
        east=cfg.east,
        north=cfg.north,
        project_id=cfg.project_id,
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
                heatmap_params(
                    gas_key=cfg.selected_gas,
                    start_date_iso=start_date_iso,
                    end_date_iso=end_date_iso,
                    west=cfg.west,
                    south=cfg.south,
                    east=cfg.east,
                    north=cfg.north,
                    project_id=cfg.project_id,
                )
            )
            st.toast("Loaded cached analysis")
            st.rerun()

    gas_cfg = get_gas_config(cfg.selected_gas)

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

    with st.spinner(
        f"Building {gas_cfg.key} time series...",
    ):
        roi = ee.Geometry.BBox(
            cfg.west, cfg.south,
            cfg.east, cfg.north,
        )

        batch_sz = BATCH_SIZE
        try:
            while True:
                try:
                    df = build_daily_timeseries(
                        gas_key=cfg.selected_gas,
                        geometry=roi,
                        start_date=start_date_iso,
                        end_date=end_date_iso,
                        batch_size=batch_sz,
                    )
                except ee.EEException as e:
                    is_concurrent = (
                        "too many concurrent"
                        in str(e).lower()
                    )
                    if is_concurrent and batch_sz >= 2:
                        batch_sz = batch_sz // 2
                        st.toast(
                            "Reducing batch size "
                            f"to {batch_sz}..."
                        )
                        continue
                    raise
                break
        except ee.EEException as exc:
            show_ee_error(exc, "Analysis failed.")
            st.stop()

    if df.empty:
        st.info(
            "No observations found for the selected "
            "gas, ROI, and date range."
        )
        st.stop()

    st.session_state["analysis_df"] = df
    st.session_state["heatmap_params"] = (
        heatmap_params(
            gas_key=cfg.selected_gas,
            start_date_iso=start_date_iso,
            end_date_iso=end_date_iso,
            west=cfg.west,
            south=cfg.south,
            east=cfg.east,
            north=cfg.north,
            project_id=cfg.project_id,
        )
    )

    analysis_cache[cache_key] = {
        "df": df.copy()
    }
    max_cache_entries = 5
    if len(analysis_cache) > max_cache_entries:
        oldest_key = next(iter(analysis_cache))
        analysis_cache.pop(oldest_key, None)
