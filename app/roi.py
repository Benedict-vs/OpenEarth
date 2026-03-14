"""ROI (Region of Interest) state management and draw-map widget."""

from __future__ import annotations

from typing import Any

import folium
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from app.config import ROI_EXAMPLES, DEFAULT_EXAMPLE


# ── Bbox state helpers ────────────────────────────────────────


def set_bbox(
    west: float, south: float,
    east: float, north: float,
) -> None:
    st.session_state["roi_west"] = west
    st.session_state["roi_south"] = south
    st.session_state["roi_east"] = east
    st.session_state["roi_north"] = north


def init_bbox_state() -> None:
    if "roi_west" in st.session_state:
        return
    w, s, e, n = ROI_EXAMPLES[DEFAULT_EXAMPLE]
    set_bbox(w, s, e, n)


def apply_pending_bbox() -> None:
    pending = st.session_state.pop("pending_bbox", None)
    if not pending:
        return
    west, south, east, north = pending
    set_bbox(west, south, east, north)


def map_center(
    west: float, south: float,
    east: float, north: float,
) -> tuple[float, float]:
    return (
        (south + north) / 2.0,
        (west + east) / 2.0,
    )


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


# ── Draw-map widget ───────────────────────────────────────────


def render_roi_draw_map(
    west: float, south: float,
    east: float, north: float,
) -> None:
    st.subheader("Draw ROI on Map")
    center_lat, center_lon = map_center(
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
