"""Stage 4 — plume masking (offline)."""

from __future__ import annotations

import numpy as np
import pytest

from openearth.ee.pixels import GridSpec, grid_for
from openearth.geometry import BBox
from openearth.methane.plume import (
    detect_plume,
    mask_outline_geojson,
    pixel_area_m2,
    robust_sigma,
)


def _grid(shape: tuple[int, int], lat: float = 38.0) -> GridSpec:
    # A ~20 m grid centered near *lat* sized to *shape*.
    h, w = shape
    bbox = BBox(54.0, lat - 0.005, 54.0 + 0.006, lat + 0.005)
    g = grid_for(bbox, 20)
    return GridSpec(x0=g.x0, y0=g.y0, xscale=g.xscale, yscale=g.yscale, width=w, height=h)


def _gauss(shape: tuple[int, int], cr: float, cc: float, sigma: float, amp: float) -> np.ndarray:
    rows, cols = np.indices(shape)
    return amp * np.exp(-(((rows - cr) ** 2 + (cols - cc) ** 2) / (2 * sigma**2)))


# ── robust_sigma / pixel_area_m2 ──


def test_robust_sigma_matches_std_for_gaussian() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(0.0, 2.0, 20000)
    assert robust_sigma(x) == pytest.approx(2.0, rel=0.05)


def test_robust_sigma_nan_aware() -> None:
    x = np.array([1.0, 2.0, np.nan, 3.0, 4.0, np.nan])
    assert np.isfinite(robust_sigma(x))


def test_pixel_area_at_lat38() -> None:
    grid = _grid((32, 32), lat=38.0)
    # Built at ~20 m square, so ~400 m² per pixel.
    assert pixel_area_m2(grid) == pytest.approx(400.0, abs=2.0)


# ── detect_plume ──


def test_recovers_gaussian_plume_area_within_25pct() -> None:
    rng = np.random.default_rng(1)
    shape = (80, 80)
    noise_sigma = 0.02
    signal = _gauss(shape, 40, 40, 6.0, amp=10 * noise_sigma)
    field = signal + rng.normal(0.0, noise_sigma, shape)
    grid = _grid(shape)

    result = detect_plume(field, grid, k_sigma=2.0)
    # Noiseless truth: pixels whose signal alone clears the k·σ threshold.
    truth = int(np.count_nonzero(signal >= 2.0 * noise_sigma))
    assert result.n_pixels == pytest.approx(truth, rel=0.25)
    assert result.area_m2 == pytest.approx(result.n_pixels * pixel_area_m2(grid))


def test_median_centered_threshold_is_offset_invariant() -> None:
    """A uniform background offset must not change the mask (fix 4a / Tier 1 F5).

    median + k·σ is shift-equivariant, so the boolean mask is bit-identical; the
    old ``field ≥ k·σ`` (measured from zero) engulfed the whole field once the
    offset cleared k·σ.
    """
    rng = np.random.default_rng(7)
    shape = (60, 60)
    base_field = _gauss(shape, 30, 30, 5.0, amp=0.4) + rng.normal(0.0, 0.02, shape)
    grid = _grid(shape)

    base = detect_plume(base_field, grid, k_sigma=2.0)
    offset = detect_plume(base_field + 0.3, grid, k_sigma=2.0)
    assert base.n_pixels > 0
    assert np.array_equal(base.mask, offset.mask)  # exactly invariant

    # The old zero-threshold behaviour would over-detect: +0.3 ≫ k·σ (σ ≈ 0.02),
    # so thresholding from zero flags essentially the whole field.
    shifted = base_field + 0.3
    over = int(np.count_nonzero(shifted >= 2.0 * robust_sigma(shifted)))
    assert over > 5 * base.n_pixels


def test_salt_noise_only_yields_empty_mask() -> None:
    rng = np.random.default_rng(2)
    shape = (64, 64)
    field = rng.normal(0.0, 1e-3, shape)
    # A dozen isolated hot pixels — opening must erase them.
    for r, c in rng.integers(0, 64, (12, 2)):
        field[r, c] = 1.0
    result = detect_plume(field, _grid(shape), k_sigma=2.0)
    assert result.n_pixels == 0
    assert not result.mask.any()


def test_min_area_filter_drops_small_components() -> None:
    shape = (40, 40)
    b = 0.01
    # A deterministic ±b checkerboard gives a nonzero robust σ (=1.4826·b) while
    # every background pixel stays below the k·σ threshold — so the only
    # above-threshold component is the injected 4-px blob (no noisy adjacency).
    rows, cols = np.indices(shape)
    field = np.where((rows + cols) % 2 == 0, b, -b).astype(float)
    field[5:7, 5:7] = 1.0  # a 4-px blob
    grid = _grid(shape)
    # min_area 5 drops the 4-px blob → empty.
    assert detect_plume(field, grid, k_sigma=2.0, min_area_px=5, opening=False).n_pixels == 0
    # min_area 3 keeps it.
    assert detect_plume(field, grid, k_sigma=2.0, min_area_px=3, opening=False).n_pixels == 4


def test_opening_removes_speckle_preserves_plume() -> None:
    shape = (48, 48)
    field = np.full(shape, 0.0)
    field[20:25, 20:25] = 1.0  # a solid 25-px plume
    for r, c in [(2, 2), (2, 40), (40, 2), (40, 40), (10, 30)]:
        field[r, c] = 1.0  # single-pixel speckle
    field += np.random.default_rng(4).normal(0.0, 1e-3, shape)
    result = detect_plume(field, _grid(shape), k_sigma=2.0, opening=True)
    assert result.mask[22, 22]
    assert not result.mask[2, 2]  # speckle gone
    assert result.n_pixels == pytest.approx(25, abs=3)


def test_component_selection_source_window_vs_peak() -> None:
    shape = (60, 60)
    field = np.zeros(shape)
    field += _gauss(shape, 15, 15, 3.0, amp=1.0)  # dimmer plume near (15,15)
    field += _gauss(shape, 45, 45, 3.0, amp=3.0)  # brighter plume near (45,45)
    field += np.random.default_rng(5).normal(0.0, 1e-3, shape)
    grid = _grid(shape)

    # No source → the peak (brighter) component.
    peak = detect_plume(field, grid, k_sigma=2.0)
    assert peak.mask[45, 45]
    assert not peak.mask[15, 15]

    # Source window over the dimmer plume selects it instead.
    src = detect_plume(field, grid, k_sigma=2.0, source_rc=(15, 15))
    assert src.mask[15, 15]
    assert not src.mask[45, 45]


# ── mask_outline_geojson ──


def test_mask_outline_geojson_within_bbox_and_closed() -> None:
    shape = (30, 30)
    grid = _grid(shape)
    mask = np.zeros(shape, dtype=bool)
    mask[10:15, 10:15] = True
    fc = mask_outline_geojson(mask, grid)
    assert fc["type"] == "FeatureCollection"
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "MultiPolygon"
    ring = geom["coordinates"][0][0]
    assert ring[0] == ring[-1]  # closed
    west, east = grid.x0, grid.x0 + grid.width * grid.xscale
    south, north = grid.y0 - grid.height * grid.yscale, grid.y0
    for lon, lat in ring:
        assert west - 1e-9 <= lon <= east + 1e-9
        assert south - 1e-9 <= lat <= north + 1e-9


def test_mask_outline_geojson_empty_mask() -> None:
    grid = _grid((10, 10))
    fc = mask_outline_geojson(np.zeros((10, 10), dtype=bool), grid)
    assert fc["features"] == []
