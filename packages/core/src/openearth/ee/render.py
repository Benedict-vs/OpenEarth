"""Image → tile URL / thumbnail / GeoTIFF URL (ported from v1 heatmap.py, de-folium'd).

Map rendering itself is the frontend's job (MapLibre consumes the XYZ URL);
this module only mints Earth Engine artifacts. Tile URLs carry an
``expires_at`` so clients can re-mint before the undocumented ~4 h expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import ee

from openearth.ee.client import ee_call
from openearth.settings import get_settings

if TYPE_CHECKING:
    from openearth.catalog.models import ProductSpec
    from openearth.geometry import ROI, BBox


def vis_params(
    spec: ProductSpec,
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> dict[str, Any]:
    """EE visualization params; *vis_min*/*vis_max* override catalog defaults."""
    if spec.is_rgb:
        return {
            "bands": spec.bands,
            "min": vis_min if vis_min is not None else spec.vis_min,
            "max": vis_max if vis_max is not None else spec.vis_max,
        }
    return {
        "min": vis_min if vis_min is not None else spec.vis_min,
        "max": vis_max if vis_max is not None else spec.vis_max,
        "palette": spec.palette,
    }


def compute_vis_range(
    image: ee.Image,
    spec: ProductSpec,
    roi: ROI | None = None,
    *,
    scale_m: int = 1000,
    headroom: float = 0.15,
) -> tuple[float, float]:
    """Compute a data-adaptive vis range from *image*.

    Uses the 0.5th and 99.5th percentiles, adds *headroom* on each side so
    extremes aren't fully clipped, and clamps to the physically plausible
    ``valid_min``/``valid_max`` from the catalog.
    """
    if spec.is_rgb:
        return (spec.vis_min, spec.vis_max)

    reducer = ee.Reducer.percentile([0.5, 99.5])
    kwargs: dict[str, Any] = {
        "reducer": reducer,
        "scale": scale_m,
        "bestEffort": True,
        "maxPixels": 1e8,
    }
    if roi is not None:
        kwargs["geometry"] = roi.to_ee_geometry()

    stats = ee_call(image.reduceRegion(**kwargs).getInfo) or {}

    band = spec.band
    p_lo = stats.get(f"{band}_p0")
    p_hi = stats.get(f"{band}_p100")
    # EE names the keys after the integer part of the percentile:
    # 0.5 → "p0", 99.5 → "p100". Fall back to explicit rounding variants.
    if p_lo is None:
        p_lo = stats.get(f"{band}_p1")
    if p_hi is None:
        p_hi = stats.get(f"{band}_p99")

    if p_lo is None or p_hi is None:
        return (spec.vis_min, spec.vis_max)

    lo = float(p_lo)
    hi = float(p_hi)
    span = hi - lo
    lo -= span * headroom
    hi += span * headroom

    clamped_min = max(lo, spec.valid_min)
    clamped_max = min(hi, spec.valid_max)

    if clamped_min >= clamped_max:
        return (spec.vis_min, spec.vis_max)

    return (clamped_min, clamped_max)


def rgb_range_stats(
    image: ee.Image,
    spec: ProductSpec,
    roi: ROI | None = None,
    *,
    scale_m: int = 100,
) -> tuple[float, float] | None:
    """Robust display percentiles of one RGB composite: ``(p1, p99)`` across bands.

    The timelapse sequence-exposure resolver samples this on a few windows to
    expose for the typical scene while keeping true highlights (snow) inside the
    minted range. Returns ``None`` when stats are unavailable (empty window).
    """
    if not spec.is_rgb or not spec.bands:
        return None
    reducer = ee.Reducer.percentile([1, 99])
    kwargs: dict[str, Any] = {
        "reducer": reducer,
        "scale": scale_m,
        "bestEffort": True,
        "maxPixels": 1e8,
    }
    if roi is not None:
        kwargs["geometry"] = roi.to_ee_geometry()

    stats = ee_call(image.select(spec.bands).reduceRegion(**kwargs).getInfo) or {}
    los: list[float] = []
    his: list[float] = []
    for band in spec.bands:
        p_lo = stats.get(f"{band}_p1")
        p_hi = stats.get(f"{band}_p99")
        if p_lo is None or p_hi is None:
            return None
        los.append(float(p_lo))
        his.append(float(p_hi))
    return (min(los), max(his))


def compute_anomaly_vis_range(
    image: ee.Image,
    band: str = "CH4_ANOMALY",
    roi: ROI | None = None,
    *,
    fallback: tuple[float, float] = (-0.08, 0.02),
) -> tuple[float, float]:
    """Median-centred symmetric vis range for an anomaly image.

    Centres the ramp on the image median so the uniform background appears
    neutral; extends symmetrically to max(median − p2, p98 − median) with
    10 % headroom. Falls back to *fallback* when stats are unavailable.
    """
    reducer = ee.Reducer.percentile([2, 50, 98])
    kwargs: dict[str, Any] = {
        "reducer": reducer,
        "scale": 100,
        "bestEffort": True,
        "maxPixels": 1e8,
    }
    if roi is not None:
        kwargs["geometry"] = roi.to_ee_geometry()

    stats = ee_call(image.reduceRegion(**kwargs).getInfo) or {}

    p02 = stats.get(f"{band}_p2")
    median = stats.get(f"{band}_p50")
    p98 = stats.get(f"{band}_p98")

    if p02 is None or median is None or p98 is None:
        return fallback

    p02, median, p98 = float(p02), float(median), float(p98)

    half = max(median - p02, p98 - median, 0.005)
    half *= 1.10  # 10 % headroom

    return (median - half, median + half)


@dataclass(frozen=True)
class TileRef:
    """A minted XYZ tile URL with its assumed expiry."""

    url: str
    expires_at: datetime
    attribution: str


def mint_tile_url(
    image: ee.Image,
    spec: ProductSpec,
    *,
    attribution: str = "Google Earth Engine",
    vis_min: float | None = None,
    vis_max: float | None = None,
) -> TileRef:
    """Mint an XYZ tile URL template for *image*.

    ``expires_at`` reflects the undocumented ~4 h getMapId lifetime
    (``OPENEARTH_TILE_TTL_SECONDS``); clients should re-mint at ~75 % TTL.
    """
    params = vis_params(spec, vis_min=vis_min, vis_max=vis_max)
    map_id = ee_call(image.getMapId, params)
    ttl = get_settings().tile_ttl_seconds
    return TileRef(
        url=map_id["tile_fetcher"].url_format,
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=ttl),
        attribution=attribution,
    )


def geo_dimensions(bbox: BBox, max_dim: int) -> str:
    """Compute ``"WIDTHxHEIGHT"`` preserving the real-world aspect ratio.

    Earth Engine's ``getThumbURL`` treats a single *dimensions* value as the
    longest edge of the *geographic* bounding box (1° lon = 1° lat in
    pixels), which stretches images at high latitude. This applies the
    cosine correction and returns an explicit ``"WxH"`` so EE renders
    undistorted output. Pure function — unit-tested offline.
    """
    aspect = bbox.aspect_ratio()
    if aspect <= 0 or aspect != aspect:  # non-positive or NaN
        return str(max_dim)

    if aspect >= 1:
        w = max_dim
        h = max(1, round(max_dim / aspect))
    else:
        h = max_dim
        w = max(1, round(max_dim * aspect))

    return f"{w}x{h}"


def thumb_url(
    image: ee.Image,
    spec: ProductSpec,
    roi: ROI,
    *,
    vis_min: float | None = None,
    vis_max: float | None = None,
    dimensions: int | str = 1024,
    img_format: str = "png",
) -> str:
    """Return a server-rendered thumbnail URL for *image* over *roi*.

    An ``int`` *dimensions* is treated as the longest edge and cosine-corrected
    into an explicit ``"WxH"`` (preserving the ROI's real-world aspect ratio); a
    ``"WxH"`` string is passed to Earth Engine verbatim — the timelapse renderer
    needs exact (even) pixel sizes shared across every frame. *img_format* must
    be ``"png"`` or ``"jpg"``.
    """
    from openearth.geometry import BBox as _BBox

    params = vis_params(spec, vis_min=vis_min, vis_max=vis_max)
    bbox = roi if isinstance(roi, _BBox) else roi.bounds
    params["region"] = roi.to_ee_geometry()
    params["dimensions"] = (
        geo_dimensions(bbox, dimensions) if isinstance(dimensions, int) else dimensions
    )
    params["format"] = img_format
    return ee_call(image.getThumbURL, params)


def download_url(
    image: ee.Image,
    spec: ProductSpec,
    roi: ROI,
    *,
    scale_m: int,
) -> str:
    """Return a GeoTIFF download URL for *image* (small areas only).

    Large exports are assembled from ``computePixels`` tiles in Phase 2's
    export module; this is the fast path.
    """
    bands = spec.bands if spec.is_rgb and spec.bands else [spec.band]
    return ee_call(
        image.getDownloadURL,
        {
            "name": f"{spec.key}_composite",
            "bands": bands,
            "region": roi.to_ee_geometry(),
            "scale": scale_m,
            "filePerBand": False,
            "format": "GEO_TIFF",
        },
    )
