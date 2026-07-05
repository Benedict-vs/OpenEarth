"""GeoTIFF export: a small-area fast path plus a windowed assembly for the rest.

Two regimes, one entry point (:func:`export_geotiff`):

- **Fast path** (small estimated size): Earth Engine's ``getDownloadURL`` mints
  a ready GeoTIFF; we stream it to disk with ``urllib`` so core keeps no HTTP
  client dependency.
- **Windowed path** (large): fetch the image tile-by-tile through
  ``computePixels`` and write each block straight into a ``rasterio`` GeoTIFF,
  so memory stays bounded no matter how big the ROI is.

Either way the output is a georeferenced EPSG:4326 GeoTIFF that opens in place
in QGIS — the Phase 2 exit criterion.
"""

from __future__ import annotations

import shutil
import urllib.request
from typing import TYPE_CHECKING

import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.windows import Window

from openearth.ee.pixels import (
    check_fetch_size,
    fetch_window,
    grid_for,
    tile_windows,
)
from openearth.ee.render import download_url
from openearth.geometry import BBox

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import ee

    from openearth.catalog.models import ProductSpec
    from openearth.ee.pixels import GridSpec
    from openearth.geometry import ROI

# Below this estimated on-disk size the single-shot getDownloadURL is faster
# and handles masking/compression for us; above it we stream windows.
FAST_PATH_MAX_BYTES = 32 * 1024 * 1024

_BYTES_PER_VALUE = 4


def _bands_for(spec: ProductSpec) -> list[str]:
    """The image bands an export writes: the RGB triplet or the single scalar."""
    return spec.bands if spec.is_rgb and spec.bands else [spec.band]


def estimate_bytes(spec: GridSpec, n_bands: int) -> int:
    """Rough uncompressed size of the full grid (float32) — picks the path."""
    return spec.width * spec.height * n_bands * _BYTES_PER_VALUE


def export_geotiff(
    image: ee.Image,
    product_spec: ProductSpec,
    roi: ROI,
    scale_m: int,
    dest: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write *image* over *roi* to *dest* as an EPSG:4326 GeoTIFF at *scale_m*.

    *on_progress(done, total)* is called per unit of work (one call for the
    fast path, one per window otherwise) so a job can report and cancel at
    window boundaries.
    """
    bbox = roi if isinstance(roi, BBox) else roi.bounds
    bands = _bands_for(product_spec)
    spec = grid_for(bbox, scale_m)

    if estimate_bytes(spec, len(bands)) <= FAST_PATH_MAX_BYTES:
        _download_geotiff(image, product_spec, roi, scale_m, dest)
        if on_progress is not None:
            on_progress(1, 1)
        return dest

    return _assemble_geotiff(image, spec, bands, dest, on_progress)


def _download_geotiff(
    image: ee.Image, product_spec: ProductSpec, roi: ROI, scale_m: int, dest: Path
) -> None:
    """Fast path: mint an EE GeoTIFF URL and stream it to *dest*."""
    # The URL is minted by Earth Engine (trusted host); stream it to disk.
    url = download_url(image, product_spec, roi, scale_m=scale_m)
    with urllib.request.urlopen(url) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out)


def _assemble_geotiff(
    image: ee.Image,
    spec: GridSpec,
    bands: list[str],
    dest: Path,
    on_progress: Callable[[int, int], None] | None,
) -> Path:
    """Windowed path: fetch each tile and write it into a rasterio GeoTIFF."""
    check_fetch_size(len(bands))
    windows = tile_windows(spec)
    total = len(windows)

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": len(bands),
        "width": spec.width,
        "height": spec.height,
        "crs": CRS.from_string(spec.crs),
        "transform": Affine(*spec.affine),
        "nodata": float("nan"),
        "compress": "deflate",
        "tiled": True,
    }

    with rasterio.open(dest, "w", **profile) as writer:
        for done, window in enumerate(windows, start=1):
            block = fetch_window(image, spec.window_grid(window), bands)
            rio_window = Window(window.col_off, window.row_off, window.width, window.height)
            for band_index in range(len(bands)):
                writer.write(block[:, :, band_index], band_index + 1, window=rio_window)
            if on_progress is not None:
                on_progress(done, total)
    return dest


__all__ = ["FAST_PATH_MAX_BYTES", "estimate_bytes", "export_geotiff"]
