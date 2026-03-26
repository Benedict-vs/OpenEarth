"""OpenEarth Explorer -- dashboard app.

Run with:  streamlit run app/main.py   (from project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Streamlit puts the script's *parent* dir (app/) on sys.path.
# We need the *project root* so that `from app.…` imports work.
_project_root = str(
    Path(__file__).resolve().parent.parent,
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import pandas as pd
import streamlit as st

from app.config import render_sidebar
from app.roi import (
    init_bbox_state,
    apply_pending_bbox,
    render_roi_draw_map,
)
from app.analysis import run_analysis
from app.tabs import (
    spatial_map,
    time_series,
    statistics,
)
from app.tabs.placeholders import (
    render_compare,
    render_animation,
    render_image,
)

# TO DO: Create information of safe limits of trace gase according to xyz
# and mask map according to safe/ unsafe limits


# ── Page config ──────────────────────────────────────

st.set_page_config(
    page_title="OpenEarth Explorer",
    layout="wide",
)
st.title("OpenEarth Explorer")
st.caption(
    "Satellite-based environmental analysis "
    "for user-defined regions."
)

init_bbox_state()
apply_pending_bbox()

# ── Sidebar ──────────────────────────────────────────

cfg = render_sidebar()

# ── ROI map (always visible) ─────────────────────────

render_roi_draw_map(
    cfg.west, cfg.south, cfg.east, cfg.north,
)

# ── Run analysis ─────────────────────────────────────

if cfg.run:
    run_analysis(cfg)

# ── Guard: stop if no results yet ────────────────────

if "analysis_df" not in st.session_state:
    st.info(
        "Configure inputs in the sidebar "
        "and click **Run analysis**."
    )
    st.stop()

# ── Tabs ─────────────────────────────────────────────

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
    "Image",
])

chart_df = st.session_state["analysis_df"].copy()
chart_df["date"] = pd.to_datetime(chart_df["date"])

with tab_spatial:
    spatial_map.render(
        chart_df, cfg.authenticate_on_fail,
    )

with tab_timeseries:
    time_series.render(
        chart_df, cfg.selected_key,
    )

with tab_compare:
    render_compare(cfg.selected_key)

with tab_stats:
    statistics.render(
        chart_df,
        cfg.selected_key,
        source=cfg.source,
    )

with tab_animation:
    render_animation()

with tab_image:
    render_image()
