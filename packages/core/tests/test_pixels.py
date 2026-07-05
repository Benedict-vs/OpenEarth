"""Pixel grid math (pure) and the offline-faked computePixels assembly.

``fetch_window``/``fetch_pixels`` are exercised without a live Earth Engine
session by faking ``ee.data.computePixels``; the fake reads only the request's
grid, so no EE object is constructed. Each fake pixel carries a value encoding
its global ``(row, col)`` so assembly and window placement can be checked
exactly.
"""

from __future__ import annotations

from typing import Any

import ee
import numpy as np
import pytest

from openearth.ee.pixels import (
    GridSpec,
    PixelWindow,
    check_fetch_size,
    fetch_pixels,
    grid_for,
    tile_windows,
)
from openearth.geometry import BBox

# ── grid_for (pure) — hand-computed against a 1000 m pixel ────

_YSCALE_1000 = 1000 / 111_320.0  # ≈ 0.0089832 deg/px, latitude-independent


def test_grid_for_equator() -> None:
    spec = grid_for(BBox(0, -0.5, 1, 0.5), 1000)
    assert spec.x0 == 0
    assert spec.y0 == 0.5
    assert spec.yscale == pytest.approx(_YSCALE_1000)
    assert spec.xscale == pytest.approx(_YSCALE_1000)  # cos(0) = 1
    assert spec.width == 112  # ceil(1 / 0.0089832)
    assert spec.height == 112


def test_grid_for_mid_latitude_widens_pixels() -> None:
    # cos(49°) ≈ 0.65606 → lon pixels ~1.52× wider than at the equator.
    spec = grid_for(BBox(8, 48.5, 9, 49.5), 1000)
    assert spec.xscale == pytest.approx(_YSCALE_1000 / np.cos(np.radians(49.0)))
    assert spec.yscale == pytest.approx(_YSCALE_1000)
    assert spec.width == 74  # ceil(1 / 0.0136926)
    assert spec.height == 112


def test_grid_for_high_latitude() -> None:
    # cos(70°) ≈ 0.34202 → far fewer lon pixels for the same span.
    spec = grid_for(BBox(0, 69.5, 1, 70.5), 1000)
    assert spec.xscale == pytest.approx(_YSCALE_1000 / np.cos(np.radians(70.0)))
    assert spec.width == 39  # ceil(1 / 0.0262656)
    assert spec.height == 112


def test_grid_for_rejects_nonpositive_scale() -> None:
    with pytest.raises(ValueError, match="scale_m"):
        grid_for(BBox(0, 0, 1, 1), 0)


def test_affine_is_north_up() -> None:
    spec = GridSpec(x0=8.0, y0=50.0, xscale=0.01, yscale=0.02, width=10, height=5)
    assert spec.affine == (0.01, 0.0, 8.0, 0.0, -0.02, 50.0)


def test_window_grid_offsets_origin() -> None:
    spec = GridSpec(x0=8.0, y0=50.0, xscale=0.01, yscale=0.02, width=100, height=100)
    grid = spec.window_grid(PixelWindow(row_off=4, col_off=8, width=6, height=3))
    at = grid["affineTransform"]
    assert grid["dimensions"] == {"width": 6, "height": 3}
    assert at["translateX"] == pytest.approx(8.0 + 8 * 0.01)
    assert at["translateY"] == pytest.approx(50.0 - 4 * 0.02)
    assert (at["scaleX"], at["scaleY"]) == (0.01, -0.02)
    assert grid["crsCode"] == "EPSG:4326"


# ── tile_windows (pure) ──────────────────────────────────────


def test_tile_windows_exact_cover_no_overlap() -> None:
    spec = GridSpec(x0=0, y0=0, xscale=1, yscale=1, width=10, height=7)
    windows = tile_windows(spec, max_px=4)

    assert len(windows) == 6  # 3 cols (4,4,2) × 2 rows (4,3)
    coverage = np.zeros((spec.height, spec.width), dtype=int)
    for w in windows:
        coverage[w.row_off : w.row_off + w.height, w.col_off : w.col_off + w.width] += 1
    assert (coverage == 1).all()  # every pixel covered exactly once


def test_tile_windows_single_window_when_small() -> None:
    spec = GridSpec(x0=0, y0=0, xscale=1, yscale=1, width=100, height=80)
    windows = tile_windows(spec, max_px=1024)
    assert windows == [PixelWindow(row_off=0, col_off=0, width=100, height=80)]


def test_tile_windows_rejects_nonpositive_max() -> None:
    spec = GridSpec(x0=0, y0=0, xscale=1, yscale=1, width=4, height=4)
    with pytest.raises(ValueError, match="max_px"):
        tile_windows(spec, max_px=0)


# ── check_fetch_size (pure) ──────────────────────────────────


def test_check_fetch_size_accepts_reasonable_request() -> None:
    check_fetch_size(6, max_px=1024)  # 25 MB — under the 48 MB window budget


def test_check_fetch_size_refuses_too_many_bands() -> None:
    with pytest.raises(ValueError, match="bands"):
        check_fetch_size(7)


def test_check_fetch_size_refuses_oversized_window() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        check_fetch_size(1, max_px=4000)  # 64 MB


def test_check_fetch_size_refuses_zero_bands() -> None:
    with pytest.raises(ValueError, match="one band"):
        check_fetch_size(0)


# ── fetch_pixels (offline-faked computePixels) ───────────────


def _install_fake_compute_pixels(
    monkeypatch: pytest.MonkeyPatch, spec: GridSpec, bands: list[str]
) -> dict[str, int]:
    """Fake computePixels so each pixel encodes its global (row, col, band).

    Value at global (R, C) for band index b is ``R + C/1000 + b`` — unique per
    location, so the assembled array pins both stitching and placement.
    """
    calls = {"n": 0}
    dtype = np.dtype([(band, "<f4") for band in bands])

    def fake(request: dict[str, Any]) -> np.ndarray:
        calls["n"] += 1
        at = request["grid"]["affineTransform"]
        dims = request["grid"]["dimensions"]
        col_off = round((at["translateX"] - spec.x0) / spec.xscale)
        row_off = round((spec.y0 - at["translateY"]) / spec.yscale)
        rows = (row_off + np.arange(dims["height"]))[:, None]
        cols = (col_off + np.arange(dims["width"]))[None, :]
        out = np.zeros((dims["height"], dims["width"]), dtype=dtype)
        for b, band in enumerate(bands):
            out[band] = rows + cols / 1000.0 + b
        return out

    monkeypatch.setattr(ee.data, "computePixels", fake)
    return calls


def _expected_cube(spec: GridSpec, n_bands: int) -> np.ndarray:
    rows = np.arange(spec.height)[:, None, None]
    cols = np.arange(spec.width)[None, :, None]
    bands = np.arange(n_bands)[None, None, :]
    return (rows + cols / 1000.0 + bands).astype(np.float32)


def test_fetch_pixels_stitches_multiple_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = GridSpec(x0=8.0, y0=50.0, xscale=0.01, yscale=0.02, width=10, height=7)
    bands = ["b1", "b2"]
    calls = _install_fake_compute_pixels(monkeypatch, spec, bands)

    cube = fetch_pixels(object(), spec, bands, max_px=4)  # image ignored by the fake

    assert calls["n"] == 6  # tiled 3×2
    assert cube.shape == (7, 10, 2)
    assert cube.dtype == np.float32
    np.testing.assert_allclose(cube, _expected_cube(spec, 2), atol=1e-4)
