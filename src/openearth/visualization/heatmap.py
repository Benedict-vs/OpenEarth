"""Spatial heatmap visualization for EE image collections."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import ee
import folium

from openearth.providers import get_collection, get_config

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
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> dict[str, Any]:
    """Return EE visualization params.

    When *vis_min* / *vis_max* are supplied they
    override the registry defaults.
    """
    cfg = get_config(data_key, source)
    return {
        "min": vis_min if vis_min is not None else cfg.vis_min,
        "max": vis_max if vis_max is not None else cfg.vis_max,
        "palette": cfg.palette,
    }


def compute_vis_range(
    image: ee.Image,
    data_key: str,
    source: str = "s5p",
    geometry: ee.Geometry | None = None,
    headroom: float = 0.15,
) -> tuple[float, float]:
    """Compute a data-adaptive vis range from *image*.

    Uses the 0.5th and 99.5th percentiles, then adds
    *headroom* (default 15 %) on each side so that
    extreme values are not fully clipped.  Results are
    clamped to the physically plausible ``valid_min`` /
    ``valid_max`` from the registry.
    """
    cfg = get_config(data_key, source)
    scale = 100 if source == "s2" else 1000

    reducer = ee.Reducer.percentile([0.5, 99.5])
    kwargs: dict[str, Any] = {
        "reducer": reducer,
        "scale": scale,
        "bestEffort": True,
        "maxPixels": 1e8,
    }
    if geometry is not None:
        kwargs["geometry"] = geometry

    stats = image.reduceRegion(**kwargs).getInfo()

    band = cfg.band
    p_lo = stats.get(f"{band}_p0")
    p_hi = stats.get(f"{band}_p100")
    # EE names the keys after the integer part of
    # the percentile: 0.5 → "p0", 99.5 → "p100".
    # Fall back to explicit rounding variants.
    if p_lo is None:
        p_lo = stats.get(f"{band}_p1")
    if p_hi is None:
        p_hi = stats.get(f"{band}_p99")

    if p_lo is None or p_hi is None:
        return (cfg.vis_min, cfg.vis_max)

    lo = float(p_lo)
    hi = float(p_hi)
    span = hi - lo
    lo = lo - span * headroom
    hi = hi + span * headroom

    clamped_min = max(lo, cfg.valid_min)
    clamped_max = min(hi, cfg.valid_max)

    if clamped_min >= clamped_max:
        return (cfg.vis_min, cfg.vis_max)

    return (clamped_min, clamped_max)


def build_mean_composite(
    data_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str = "s5p",
) -> ee.Image:
    """Pixel-wise mean image over the full date range."""
    cfg = get_config(data_key, source)
    collection = get_collection(
        data_key, geometry,
        start_date, end_date, source,
    )
    image = collection.mean().select(cfg.band)
    if not _is_global(geometry):
        image = image.clip(geometry)
    return image


def build_date_composite(
    data_key: str,
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    half_window_days: int = 3,
    source: str = "s5p",
) -> ee.Image:
    """Short-window mean composite centred on *target_date*."""
    cfg = get_config(data_key, source)

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

    collection = get_collection(
        data_key,
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
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> str:
    """Return an XYZ tile URL template for *image*."""
    params = get_vis_params(
        data_key, source,
        vis_min=vis_min, vis_max=vis_max,
    )
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


def _geo_dimensions(
    geometry: ee.Geometry,
    max_dim: int,
) -> str:
    """Compute ``"WIDTHxHEIGHT"`` preserving real-world aspect ratio.

    Earth Engine's ``getThumbURL`` treats a single *dimensions*
    value as the longest edge in the *geographic* bounding box,
    which means 1° lon = 1° lat in pixels.  At higher latitudes
    this stretches the image horizontally because 1° of longitude
    is physically shorter than 1° of latitude.

    This helper corrects for that by computing the real-world
    width/height ratio (cosine correction) and returning an
    explicit ``"WxH"`` string so EE renders undistorted output.
    """
    coords = geometry.bounds().coordinates().getInfo()
    ring = coords[0]
    lons = [float(p[0]) for p in ring]
    lats = [float(p[1]) for p in ring]

    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)

    mid_lat_rad = math.radians((south + north) / 2)
    # Real-world extent in degrees, corrected
    width_deg = (east - west) * math.cos(mid_lat_rad)
    height_deg = north - south

    if (
        width_deg <= 0
        or height_deg <= 0
        or not math.isfinite(width_deg)
        or not math.isfinite(height_deg)
    ):
        return str(max_dim)

    aspect = width_deg / height_deg
    if not math.isfinite(aspect):
        return str(max_dim)

    if aspect >= 1:
        w = max_dim
        h = max(1, round(max_dim / aspect))
    else:
        h = max_dim
        w = max(1, round(max_dim * aspect))

    return f"{w}x{h}"


def get_thumb_url(
    image: ee.Image,
    data_key: str,
    geometry: ee.Geometry,
    source: str = "s5p",
    vis_min: float | None = None,
    vis_max: float | None = None,
    dimensions: int = 1024,
    img_format: str = "png",
) -> str:
    """Return a thumbnail URL for *image* clipped to *geometry*.

    The image is rendered server-side by Earth Engine.
    Pixel dimensions are computed to preserve the real-world
    aspect ratio of the ROI (cosine-corrected for latitude).
    *img_format* must be ``"png"`` or ``"jpg"``.
    """
    params = get_vis_params(
        data_key, source,
        vis_min=vis_min, vis_max=vis_max,
    )
    params["region"] = geometry
    params["dimensions"] = _geo_dimensions(
        geometry, dimensions,
    )
    params["format"] = img_format
    return image.getThumbURL(params)


def get_download_url(
    image: ee.Image,
    data_key: str,
    geometry: ee.Geometry,
    source: str = "s5p",
    scale: int | None = None,
) -> str:
    """Return a GeoTIFF download URL for *image*.

    Uses a default *scale* of 1000 m for S5P and
    100 m for S2 unless overridden.
    """
    cfg = get_config(data_key, source)
    if scale is None:
        scale = 100 if source == "s2" else 1000
    return image.getDownloadURL({
        "name": f"{cfg.key}_composite",
        "bands": [cfg.band],
        "region": geometry,
        "scale": scale,
        "filePerBand": False,
        "format": "GEO_TIFF",
    })


def create_heatmap_folium(
    tile_url: str,
    center_lat: float,
    center_lon: float,
    bounds: list[list[float]] | None = None,
    layer_name: str = "Heatmap",
    source: str = "s5p",
) -> tuple[folium.Map, folium.FeatureGroup]:
    """Build a folium Map with an EE tile overlay.

    Returns a stable base map and a dynamic FeatureGroup containing the
    data tile layer and ROI rectangle.  The caller should pass the
    FeatureGroup via ``st_folium(feature_group_to_add=...)`` so that
    layer changes do not remount the component (preserving zoom/pan).
    """
    is_global = (
        isinstance(bounds, list)
        and len(bounds) == 2
        and _bounds_are_global(bounds)
    )

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=2 if is_global else 10,
        tiles=None,
    )
    folium.TileLayer(
        tiles="CartoDB positron",
        name="Background map",
        overlay=True,
        control=True,
    ).add_to(fmap)
    if (
        not is_global
        and isinstance(bounds, list)
        and len(bounds) == 2
    ):
        fmap.fit_bounds(bounds)

    fg = folium.FeatureGroup(name=layer_name)
    folium.TileLayer(
        tiles=tile_url,
        attr=_ATTR.get(source, _ATTR["s5p"]),
        name=layer_name,
        overlay=True,
        control=True,
        opacity=0.75,
    ).add_to(fg)

    if bounds and not is_global:
        folium.Rectangle(
            bounds=bounds,
            color="#333333",
            weight=2,
            fill=False,
            dash_array="6",
            tooltip="Analysis ROI",
        ).add_to(fg)

    return fmap, fg
