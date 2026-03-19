"""Spatial heatmap visualization for EE image collections."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import ee
import folium

from openearth.providers.gee_s2 import get_s2_collection
from openearth.providers.gee_s5p import (
    get_trace_gas_collection,
)
from openearth.providers.s2_registry import (
    get_s2_index_config,
)
from openearth.providers.s5p_registry import (
    get_gas_config,
)

_ATTR = {
    "s5p": (
        "Google Earth Engine / "
        "Copernicus Sentinel-5P"
    ),
    "s2": (
        "Google Earth Engine / "
        "Copernicus Sentinel-2"
    ),
}


def _get_config(data_key: str, source: str):
    """Return the registry config for *data_key*."""
    if source == "s2":
        return get_s2_index_config(data_key)
    return get_gas_config(data_key)


def _get_collection(
    data_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str,
) -> ee.ImageCollection:
    """Return the ImageCollection for *source*."""
    if source == "s2":
        return get_s2_collection(
            data_key, geometry,
            start_date, end_date,
        )
    return get_trace_gas_collection(
        data_key, geometry,
        start_date, end_date,
    )


def _is_global(geometry: ee.Geometry) -> bool:
    """Return True if *geometry* covers the whole Earth.

    Comparing the bounding box to +/-180/+/-90 avoids
    the expensive server-side ``.clip()`` call that
    chokes Earth Engine when the ROI is the entire planet.
    """
    coords = geometry.bounds().coordinates().getInfo()
    ring = coords[0]  # exterior ring
    lons = [float(p[0]) for p in ring]
    lats = [float(p[1]) for p in ring]
    return (
        min(lons) <= -179
        and max(lons) >= 179
        and min(lats) <= -89
        and max(lats) >= 89
    )


def get_vis_params(
    data_key: str,
    source: str = "s5p",
) -> dict[str, Any]:
    """Return EE visualization params."""
    cfg = _get_config(data_key, source)
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
    source: str = "s5p",
) -> ee.Image:
    """Pixel-wise mean image over the full date range."""
    cfg = _get_config(gas_key, source)
    collection = _get_collection(
        gas_key, geometry,
        start_date, end_date, source,
    )
    image = collection.mean().select(cfg.band)
    if not _is_global(geometry):
        image = image.clip(geometry)
    return image


def build_date_composite(
    gas_key: str,
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    half_window_days: int = 3,
    source: str = "s5p",
) -> ee.Image:
    """Short-window mean composite centred on *target_date*."""
    cfg = _get_config(gas_key, source)

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

    collection = _get_collection(
        gas_key,
        geometry,
        window_start.isoformat(),
        window_end.isoformat(),
        source,
    )
    image = collection.mean().select(cfg.band)
    if not _is_global(geometry):
        image = image.clip(geometry)
    return image


def get_tile_url(
    image: ee.Image,
    data_key: str,
    source: str = "s5p",
) -> str:
    """Return an XYZ tile URL template for *image*."""
    params = get_vis_params(data_key, source)
    map_id_dict = image.getMapId(params)
    return map_id_dict["tile_fetcher"].url_format


def _bounds_are_global(
    bounds: list[list[float]],
) -> bool:
    """True if the bounds cover the full Earth."""
    (south, west), (north, east) = bounds
    return (
        west <= -179
        and east >= 179
        and south <= -89
        and north >= 89
    )


def create_heatmap_folium(
    tile_url: str,
    center_lat: float,
    center_lon: float,
    bounds: list[list[float]] | None = None,
    layer_name: str = "Heatmap",
    source: str = "s5p",
) -> folium.Map:
    """Build a folium Map with an EE tile overlay."""
    is_global = (
        isinstance(bounds, list)
        and len(bounds) == 2
        and _bounds_are_global(bounds)
    )

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=2 if is_global else None,
        tiles="CartoDB positron",
    )
    if (
        not is_global
        and isinstance(bounds, list)
        and len(bounds) == 2
    ):
        fmap.fit_bounds(bounds)

    folium.TileLayer(
        tiles=tile_url,
        attr=_ATTR.get(source, _ATTR["s5p"]),
        name=layer_name,
        overlay=True,
        control=True,
        opacity=0.7,
    ).add_to(fmap)

    if bounds and not is_global:
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
