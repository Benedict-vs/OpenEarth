"""Georeferenced pixel fetch via ``ee.data.computePixels`` (pulled forward from
Phase 3 — exports need it now; the retrieval chips will reuse it unchanged).

The split mirrors the rest of the library: the grid math is pure and
unit-tested offline (``GridSpec``, ``grid_for``, ``tile_windows``); only
``fetch_window``/``fetch_pixels`` touch Earth Engine, and they do so through
``ee_call`` like every other blocking round-trip.

We fetch in an explicit EPSG:4326 grid (west→east, north→south) at a chosen
metres-per-pixel, tiled into ``computePixels`` windows so a large export never
requests one oversized payload. The request shape is the modern
``earthengine-api`` dict API (verified against ``earthengine-api>=1.7``):
``{expression: ee.Image, fileFormat: "NUMPY_NDARRAY", grid: {...}, bandIds}``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import ee
import numpy as np

from openearth.ee.client import ee_call

if TYPE_CHECKING:
    from openearth.geometry import BBox

# One degree of latitude in metres (WGS84 mean). Longitude shrinks by cos(lat);
# the same constant backs the web-side pixel box in apps/web/src/lib/geo.ts.
_M_PER_DEG = 111_320.0

# computePixels returns float bands as float32; used to estimate payload size.
_BYTES_PER_VALUE = 4

# Pre-request safety net (a windowed export can't otherwise bound its cost):
# refuse pathological band counts or a single window that would exceed ~48 MB.
MAX_BANDS = 6
MAX_WINDOW_BYTES = 48 * 1024 * 1024


@dataclass(frozen=True)
class PixelWindow:
    """A rectangular block of a :class:`GridSpec`, in pixel offsets."""

    row_off: int
    col_off: int
    width: int
    height: int


@dataclass(frozen=True)
class GridSpec:
    """An axis-aligned pixel grid in a projected CRS.

    Geometry is a north-up affine: pixel (0, 0) is the top-left corner at
    ``(x0, y0)``; longitude increases east by ``xscale`` per column, latitude
    decreases south by ``yscale`` per row. ``xscale``/``yscale`` are the
    degrees-per-pixel magnitudes (both positive).
    """

    x0: float
    y0: float
    xscale: float
    yscale: float
    width: int
    height: int
    crs: str = "EPSG:4326"

    @property
    def affine(self) -> tuple[float, float, float, float, float, float]:
        """GDAL/rasterio affine ``(a, b, c, d, e, f)`` for this north-up grid."""
        return (self.xscale, 0.0, self.x0, 0.0, -self.yscale, self.y0)

    def window_grid(self, window: PixelWindow) -> dict[str, Any]:
        """The ``computePixels`` ``grid`` payload for one window of this grid."""
        x = self.x0 + window.col_off * self.xscale
        y = self.y0 - window.row_off * self.yscale
        return {
            "dimensions": {"width": window.width, "height": window.height},
            "affineTransform": {
                "scaleX": self.xscale,
                "shearX": 0.0,
                "translateX": x,
                "shearY": 0.0,
                "scaleY": -self.yscale,
                "translateY": y,
            },
            "crsCode": self.crs,
        }


def grid_for(bbox: BBox, scale_m: int) -> GridSpec:
    """Build an EPSG:4326 grid covering *bbox* at ~*scale_m* metres per pixel.

    Longitude spacing is cosine-corrected at the box's centre latitude so
    pixels are roughly *scale_m* square on the ground; the grid is sized up
    (``ceil``) so it fully covers the box.
    """
    if scale_m <= 0:
        raise ValueError(f"scale_m must be positive; got {scale_m}.")

    center_lat, _ = bbox.center
    yscale = scale_m / _M_PER_DEG
    xscale = scale_m / (_M_PER_DEG * math.cos(math.radians(center_lat)))

    width = max(1, math.ceil(bbox.width_deg / xscale))
    height = max(1, math.ceil(bbox.height_deg / yscale))
    return GridSpec(
        x0=bbox.west, y0=bbox.north, xscale=xscale, yscale=yscale, width=width, height=height
    )


def tile_windows(spec: GridSpec, max_px: int = 1024) -> list[PixelWindow]:
    """Tile *spec* into a row-major list of ≤ ``max_px`` square windows.

    The windows exactly cover the grid with no overlap; edge windows are
    clipped to the remaining pixels.
    """
    if max_px <= 0:
        raise ValueError(f"max_px must be positive; got {max_px}.")

    windows: list[PixelWindow] = []
    for row_off in range(0, spec.height, max_px):
        h = min(max_px, spec.height - row_off)
        for col_off in range(0, spec.width, max_px):
            w = min(max_px, spec.width - col_off)
            windows.append(PixelWindow(row_off=row_off, col_off=col_off, width=w, height=h))
    return windows


def check_fetch_size(n_bands: int, max_px: int = 1024) -> None:
    """Refuse a fetch whose band count or per-window payload is out of budget.

    Raises :class:`ValueError` *before* any Earth Engine request is issued.
    """
    if n_bands < 1:
        raise ValueError("At least one band is required.")
    if n_bands > MAX_BANDS:
        raise ValueError(f"Refusing to fetch {n_bands} bands (limit {MAX_BANDS}).")
    est = max_px * max_px * n_bands * _BYTES_PER_VALUE
    if est > MAX_WINDOW_BYTES:
        raise ValueError(
            f"Estimated window payload {est / 1e6:.0f} MB exceeds the "
            f"{MAX_WINDOW_BYTES / 1e6:.0f} MB limit; use a smaller max_px or fewer bands."
        )


def fetch_window(image: ee.Image, grid: dict[str, Any], bands: list[str]) -> np.ndarray:
    """Fetch one *grid* window of *image* as a ``(H, W, B)`` float32 array.

    ``computePixels`` returns a structured array with one field per band
    (masked pixels arrive as EE's fill value); we stack the fields into a
    plain float32 cube in ``bands`` order.
    """
    request = {
        "expression": image,
        "fileFormat": "NUMPY_NDARRAY",
        "grid": grid,
        "bandIds": list(bands),
    }
    arr = ee_call(ee.data.computePixels, request)
    return np.stack([arr[band].astype(np.float32) for band in bands], axis=-1)


def fetch_pixels(
    image: ee.Image, spec: GridSpec, bands: list[str], *, max_px: int = 1024
) -> np.ndarray:
    """Assemble the whole *spec* grid of *image* into one ``(H, W, B)`` array.

    Windows are fetched and stitched in memory — fine for the in-memory
    consumers (Phase 3 retrieval chips); the exporter streams window-by-window
    instead so its footprint stays bounded.
    """
    check_fetch_size(len(bands), max_px)
    out = np.full((spec.height, spec.width, len(bands)), np.nan, dtype=np.float32)
    for window in tile_windows(spec, max_px):
        block = fetch_window(image, spec.window_grid(window), bands)
        out[
            window.row_off : window.row_off + window.height,
            window.col_off : window.col_off + window.width,
            :,
        ] = block
    return out


__all__ = [
    "GridSpec",
    "PixelWindow",
    "check_fetch_size",
    "fetch_pixels",
    "fetch_window",
    "grid_for",
    "tile_windows",
]
