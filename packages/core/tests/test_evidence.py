"""False-positive evidence checks (Phase 9): dimming sign, surface correlation, chip flags."""

from __future__ import annotations

import numpy as np
import pytest

from openearth.methane.evidence import (
    SURFACE_CORRELATION_CUT,
    b12_dimming_ok,
    chip_flags,
    surface_correlation,
)


def _mask(shape: tuple[int, int]) -> np.ndarray:
    m = np.zeros(shape, dtype=bool)
    m[4:8, 4:8] = True
    return m


def test_b12_dimming_ok_sign() -> None:
    shape = (12, 12)
    mask = _mask(shape)
    dimming = np.full(shape, 0.01)
    dimming[mask] = -0.05  # a real plume absorbs → negative in-mask ΔR
    assert b12_dimming_ok(dimming, mask)

    brightening = np.full(shape, 0.01)
    brightening[mask] = 0.05  # non-negative in-mask → not a plume
    assert not b12_dimming_ok(brightening, mask)


def test_b12_dimming_ok_empty_mask_is_ok() -> None:
    shape = (6, 6)
    empty = np.zeros(shape, dtype=bool)
    assert b12_dimming_ok(np.full(shape, 0.05), empty)  # nothing to assess → ok


def test_surface_correlation_flags_visible_feature() -> None:
    shape = (16, 16)
    mask = _mask(shape)
    # A band that is bright exactly where the mask is → strong correlation.
    visible = np.zeros(shape)
    visible[mask] = 0.5
    r = surface_correlation(mask, {"B4": visible, "B3": np.zeros(shape), "B2": np.zeros(shape)})
    assert r > SURFACE_CORRELATION_CUT
    # Perfectly separable indicator vs band; float summation order is
    # platform-dependent, so compare within machine epsilon rather than exactly.
    assert r == pytest.approx(1.0)


def test_surface_correlation_rgb_invisible_plume_is_low() -> None:
    rng = np.random.default_rng(1)
    shape = (16, 16)
    mask = _mask(shape)
    noise = {b: rng.uniform(0.1, 0.11, shape) for b in ("B4", "B3", "B2")}
    assert surface_correlation(mask, noise) < SURFACE_CORRELATION_CUT


def test_surface_correlation_empty_mask_is_zero() -> None:
    shape = (8, 8)
    empty = np.zeros(shape, dtype=bool)
    bands = {b: np.ones(shape) for b in ("B4", "B3", "B2")}
    assert surface_correlation(empty, bands) == 0.0


def test_chip_flags() -> None:
    shape = (10, 10)
    clean = {"B12": np.full(shape, 0.3), "B2": np.full(shape, 0.1)}
    assert chip_flags(clean) == []

    sparse = {
        "B12": np.where(np.arange(100).reshape(shape) < 40, 0.3, np.nan),
        "B2": np.full(shape, 0.1),
    }
    assert "sparse_chip" in chip_flags(sparse)

    cloudy = {"B12": np.full(shape, 0.3), "B2": np.full(shape, 0.4)}
    assert "cloudy_chip" in chip_flags(cloudy)
