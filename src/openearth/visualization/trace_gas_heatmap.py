"""Generic trace-gas spatial heatmap visualization."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import ee
import folium

from openearth.providers.gas_registry import (
    get_gas_config,
)
from openearth.providers.gee_trace_gas import (
    get_trace_gas_collection,
)


def get_vis_params(gas_key: str) -> dict[str, Any]:
    """Return EE visualization params for *gas_key*."""
    cfg = get_gas_config(gas_key)
    return {
        "min": cfg.vis_min,
        "max": cfg.vis_max,
        "palette": cfg.palette,
    }


def build_mean_composite(
    gas_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.Image:
    """Pixel-wise mean image over the full date range."""
    cfg = get_gas_config(gas_key)
    collection = get_trace_gas_collection(
        gas_key, geometry, start_date, end_date,
    )
    return (
        collection.mean()
        .select(cfg.band)
        .clip(geometry)
    )


def build_date_composite(
    gas_key: str,
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    half_window_days: int = 3,
) -> ee.Image:
    """Short-window mean composite centred on *target_date*."""
    cfg = get_gas_config(gas_key)

    if isinstance(target_date, str):
        target_date = date.fromisoformat(
            target_date,
        )
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    window_start = target_date - timedelta(
        days=half_window_days,
    )
    window_end = target_date + timedelta(
        days=half_window_days + 1,
    )

    collection = get_trace_gas_collection(
        gas_key,
        geometry,
        window_start.isoformat(),
        window_end.isoformat(),
    )
    return (
        collection.mean()
        .select(cfg.band)
        .clip(geometry)
    )


def get_tile_url(
    image: ee.Image,
    gas_key: str,
) -> str:
    """Return an XYZ tile URL template for *image*.

    Uses per-gas visualization parameters from the
    registry.
    """
    params = get_vis_params(gas_key)
    map_id_dict = image.getMapId(params)
    return map_id_dict["tile_fetcher"].url_format


def create_heatmap_folium(
    tile_url: str,
    center_lat: float,
    center_lon: float,
    zoom: int,
    bounds: list[list[float]] | None = None,
    layer_name: str = "Trace Gas Heatmap",
) -> folium.Map:
    """Build a folium Map with an EE tile overlay."""
    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="CartoDB positron",
    )

    folium.TileLayer(
        tiles=tile_url,
        attr=(
            "Google Earth Engine / "
            "Copernicus Sentinel-5P"
        ),
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
