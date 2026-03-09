"""NO2 spatial heatmap visualization using Earth Engine tile serving."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import ee
import folium

from openearth.providers.gee_no2 import NO2_BAND, get_no2_collection

# ── Color palette ──────────────────────────────────────────────
# 10-stop Spectral-derived diverging palette: blue (clean) to red (polluted).
# Perceptually ordered and colorblind-friendly.
NO2_VIS_PALETTE = [
    "#5e4fa2",  # deep purple-blue  (lowest)
    "#3288bd",  # medium blue
    "#66c2a5",  # teal-green
    "#abdda4",  # light green
    "#e6f598",  # yellow-green
    "#fee08b",  # light yellow-orange
    "#fdae61",  # orange
    "#f46d43",  # red-orange
    "#d53e4f",  # dark red
    "#9e0142",  # deep crimson       (highest)
]

# Typical tropospheric NO2 column number density range (mol/m^2).
NO2_VIS_MIN = 0.0
NO2_VIS_MAX = 0.0002

DEFAULT_VIS_PARAMS: dict[str, Any] = {
    "min": NO2_VIS_MIN,
    "max": NO2_VIS_MAX,
    "palette": NO2_VIS_PALETTE,
}


def build_mean_composite(
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.Image:
    """Return the pixel-wise mean NO2 image over the full date range."""
    collection = get_no2_collection(geometry, start_date, end_date)
    return collection.mean().select(NO2_BAND).clip(geometry)


def build_date_composite(
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    half_window_days: int = 3,
) -> ee.Image:
    """Return a short-window mean composite centered on *target_date*.

    Uses +/- half_window_days to increase spatial coverage (e.g.
    half_window_days=3 gives a 7-day window).
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    window_start = target_date - timedelta(days=half_window_days)
    # end date is exclusive
    window_end = target_date + timedelta(
        days=half_window_days + 1,
    )

    collection = get_no2_collection(
        geometry,
        window_start.isoformat(),
        window_end.isoformat(),
    )
    return collection.mean().select(NO2_BAND).clip(geometry)


def get_tile_url(
    image: ee.Image,
    vis_params: dict[str, Any] | None = None,
) -> str:
    """Return an XYZ tile URL template for the given EE image.

    The returned string contains {x}, {y}, {z} placeholders
    suitable for folium.TileLayer.
    """
    params = vis_params or DEFAULT_VIS_PARAMS
    map_id_dict = image.getMapId(params)
    return map_id_dict["tile_fetcher"].url_format


def create_heatmap_folium(
    tile_url: str,
    center_lat: float,
    center_lon: float,
    zoom: int,
    bounds: list[list[float]] | None = None,
    layer_name: str = "NO2 Heatmap",
) -> folium.Map:
    """Build a folium Map with the EE NO2 tile overlay."""
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="CartoDB positron",
    )

    folium.TileLayer(
        tiles=tile_url,
        attr="Google Earth Engine / Copernicus Sentinel-5P",
        name=layer_name,
        overlay=True,
        control=True,
        opacity=0.7,
    ).add_to(fmap)

    if bounds:
        folium.Rectangle(
            bounds=bounds,
            color="#333333",
            weight=2,
            fill=False,
            dash_array="6",
            tooltip="Analysis ROI",
        ).add_to(fmap)

    folium.LayerControl().add_to(fmap)
    return fmap
