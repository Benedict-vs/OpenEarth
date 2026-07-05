"""GeoTIFF export: the fast download path and the windowed rasterio assembly.

The windowed path is the offline proxy for the "opens georeferenced in QGIS"
exit criterion: a faked ``computePixels`` supplies synthetic gradients, the
exporter writes a real GeoTIFF, and we re-open it to assert CRS, transform, and
pixel values survive the round-trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ee
import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

import openearth.export as export_mod
from openearth.catalog import get_dataset
from openearth.ee.pixels import grid_for
from openearth.export import export_geotiff
from openearth.geometry import BBox

NDVI = get_dataset("s2").get("NDVI")  # single scalar band, not RGB


def _write_source_tif(path: Path) -> None:
    """A tiny valid GeoTIFF for the fast path to 'download' via file://."""
    data = np.arange(25, dtype=np.float32).reshape(5, 5)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        dtype="float32",
        count=1,
        width=5,
        height=5,
        crs="EPSG:4326",
        transform=Affine(0.1, 0, 8.0, 0, -0.1, 50.0),
    ) as dst:
        dst.write(data, 1)


def test_fast_path_streams_download_to_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src.tif"
    _write_source_tif(src)
    monkeypatch.setattr(export_mod, "download_url", lambda *a, **k: src.as_uri())

    progress: list[tuple[int, int]] = []
    dest = tmp_path / "out.tif"
    # A small ROI keeps the estimate under the fast-path threshold.
    result = export_geotiff(
        object(),
        NDVI,
        BBox(8, 49, 8.05, 49.05),
        100,
        dest,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert result == dest
    assert dest.read_bytes() == src.read_bytes()  # streamed verbatim
    assert progress == [(1, 1)]


def _install_fake_compute_pixels(monkeypatch: pytest.MonkeyPatch, spec: Any) -> None:
    """Each pixel encodes its global (row, col): value = row + col/1000."""

    def fake(request: dict[str, Any]) -> np.ndarray:
        at = request["grid"]["affineTransform"]
        dims = request["grid"]["dimensions"]
        col_off = round((at["translateX"] - spec.x0) / spec.xscale)
        row_off = round((spec.y0 - at["translateY"]) / spec.yscale)
        rows = (row_off + np.arange(dims["height"]))[:, None]
        cols = (col_off + np.arange(dims["width"]))[None, :]
        out = np.zeros((dims["height"], dims["width"]), dtype=np.dtype([("NDVI", "<f4")]))
        out["NDVI"] = rows + cols / 1000.0
        return out

    monkeypatch.setattr(ee.data, "computePixels", fake)


def test_windowed_path_writes_georeferenced_geotiff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the windowed path regardless of size, and make the grid wide enough
    # to span two 1024 px windows so cross-window stitching is exercised.
    monkeypatch.setattr(export_mod, "FAST_PATH_MAX_BYTES", 0)
    bbox = BBox(0.0, 0.0, 1.0, 0.0028)
    spec = grid_for(bbox, 100)
    assert spec.width > 1024  # two windows across
    _install_fake_compute_pixels(monkeypatch, spec)

    progress: list[tuple[int, int]] = []
    dest = tmp_path / "big.tif"
    export_geotiff(
        object(),
        NDVI,
        bbox,
        100,
        dest,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(1, 2), (2, 2)]  # two windows, in order

    with rasterio.open(dest) as src:
        assert src.width == spec.width
        assert src.height == spec.height
        assert src.count == 1
        assert src.crs.to_epsg() == 4326
        assert tuple(src.transform)[:6] == pytest.approx(spec.affine)
        assert np.isnan(src.nodata)
        rows = np.arange(spec.height)[:, None]
        cols = np.arange(spec.width)[None, :]
        np.testing.assert_allclose(src.read(1), rows + cols / 1000.0, atol=1e-4)
