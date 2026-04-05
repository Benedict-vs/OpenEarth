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

import streamlit as st

from app.config import render_sidebar
from app.roi import (
    init_bbox_state,
    apply_pending_bbox,
    render_roi_draw_map,
)
from app.analysis import init_session
from app.tabs import (
    spatial_map,
    time_series,
    statistics,
)


# ── Satellite info panel ──────────────────────────────────────

_SATELLITE_INFO: dict[str, dict] = {
    "s5p": {
        "full_name": "Sentinel-5P / TROPOMI",
        "agency": "ESA / Copernicus",
        "launched": "Oct 2017",
        "orbit_altitude": "824 km",
        "revisit": "~1 day (global)",
        "swath": "2,600 km",
        "resolution": "5.5 × 3.5 km (NO₂/SO₂/O₃/HCHO) · 7 × 7 km (CO/CH₄)",
        "sensor": "TROPOMI (UV–SWIR spectrometer)",
        "cloud_penetration": False,
        "applications": (
            "Air quality monitoring, volcanic SO₂ plumes, "
            "wildfire CO emissions, stratospheric ozone, "
            "agricultural CH₄ and HCHO."
        ),
        "notes": (
            "TROPOMI is the most sensitive spaceborne air-quality sensor "
            "to date. Level-3 offline (OFFL) products are typically "
            "available within a few days of acquisition."
        ),
    },
    "s2": {
        "full_name": "Sentinel-2 A/B / MSI",
        "agency": "ESA / Copernicus",
        "launched": "S-2A Jun 2015 · S-2B Mar 2017",
        "orbit_altitude": "786 km",
        "revisit": "5 days (dual satellite) · 10 days (single)",
        "swath": "290 km",
        "resolution": "10 m (B2/B3/B4/B8) · 20 m (B5–B7/B8a/B11/B12) · 60 m (B1/B9/B10)",
        "sensor": "MSI — 13 spectral bands (443–2190 nm)",
        "cloud_penetration": False,
        "applications": (
            "Vegetation mapping (NDVI/EVI), land-use classification, "
            "water body detection (NDWI), agricultural monitoring, "
            "forest disturbance, coastal and inland water quality."
        ),
        "notes": (
            "Cloud masking is applied using the Sentinel-2 cloud "
            "probability product (s2cloudless). Coverage: 56°S – 84°N. "
            "SWIR bands (B11/B12) are sensitive to methane absorption "
            "and are used as CH₄ proxy indices."
        ),
    },
    "s1": {
        "full_name": "Sentinel-1 A/C / SAR C-band",
        "agency": "ESA / Copernicus",
        "launched": "S-1A Apr 2014 · S-1C Dec 2024",
        "orbit_altitude": "693 km",
        "revisit": "6 days (Europe) · 12 days (global, single satellite)",
        "swath": "250 km (IW mode)",
        "resolution": "10 m (IW GRD)",
        "sensor": "C-SAR — C-band (5.405 GHz, λ ≈ 5.6 cm)",
        "cloud_penetration": True,
        "applications": (
            "Flood extent mapping, deforestation / forest loss, "
            "soil moisture estimation, sea-ice monitoring, "
            "ship and infrastructure detection, subsidence tracking."
        ),
        "notes": (
            "SAR is an active sensor and works day and night regardless "
            "of cloud cover or smoke. This app uses IW (Interferometric "
            "Wide) mode GRD products with VV + VH dual polarisation. "
            "Backscatter values are in logarithmic dB scale."
        ),
    },
}


def _render_satellite_info(source: str) -> None:
    """Render a concise info panel for the selected satellite."""
    info = _SATELLITE_INFO.get(source)
    if info is None:
        return

    with st.expander(
        f"About {info['full_name']}",
        expanded=True,
    ):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Revisit interval", info["revisit"])
        col2.metric("Ground resolution", info["resolution"].split(" · ")[0])
        col3.metric("Swath width", info["swath"])
        col4.metric("Orbit altitude", info["orbit_altitude"])

        st.caption(
            f"**Sensor:** {info['sensor']}  ·  "
            f"**Launched:** {info['launched']}  ·  "
            f"**Agency:** {info['agency']}"
            + (
                "  ·  ☁️ **Cloud-penetrating (SAR)**"
                if info["cloud_penetration"]
                else ""
            )
        )
        if len(info["resolution"].split(" · ")) > 1:
            st.caption(f"**Full resolution detail:** {info['resolution']}")

        st.markdown(f"**Key applications:** {info['applications']}")
        st.caption(info["notes"])

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

# ── ROI map (collapsible) ─────────────────────────────

# Default: visible before first analysis, hidden after.
has_results = "heatmap_params" in st.session_state
if "show_roi_map" not in st.session_state:
    st.session_state["show_roi_map"] = not has_results

# Hide map on analysis run (must happen before widget).
if cfg.run:
    st.session_state["show_roi_map"] = False

st.checkbox(
    "Show ROI map",
    key="show_roi_map",
)

if st.session_state["show_roi_map"]:
    drawn_bbox = render_roi_draw_map(
        cfg.west, cfg.south, cfg.east, cfg.north,
    )
    if drawn_bbox is not None:
        prev = st.session_state.get("drawn_bbox")
        st.session_state["drawn_bbox"] = drawn_bbox
        if prev != drawn_bbox:
            st.rerun()

# ── Initialize session (fast) ────────────────────────

if cfg.run:
    init_session(cfg)

# ── Guard: stop if map not loaded yet ────────────────

if "heatmap_params" not in st.session_state:
    _render_satellite_info(cfg.source)
    st.info(
        "Configure inputs in the sidebar "
        "and click **Load Map**."
    )
    st.stop()

# ── Tabs ──────────────────────���──────────────────────

(
    tab_spatial,
    tab_timeseries,
    tab_stats,
) = st.tabs([
    "Spatial Map",
    "Time Series",
    "Statistics",
])

with tab_spatial:
    spatial_map.render(
        cfg.authenticate_on_fail,
    )

with tab_timeseries:
    time_series.render(
        cfg.selected_keys[0],
        source=cfg.source,
    )

with tab_stats:
    statistics.render(
        cfg.selected_keys[0],
        source=cfg.source,
    )
