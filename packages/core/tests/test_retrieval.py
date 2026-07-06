"""Stage 3 — calibrated MBSP/MBMP retrieval (offline)."""

from __future__ import annotations

import numpy as np
import pytest

from openearth.geometry import BBox
from openearth.methane.retrieval import (
    _FILL,
    CHIP_BANDS,
    MbspResult,
    _fill_to_reflectance,
    fetch_chip,
    mbmp,
    mbsp,
)


def _gauss(shape: tuple[int, int], cr: int, cc: int, sigma: float) -> np.ndarray:
    rows, cols = np.indices(shape)
    return np.exp(-(((rows - cr) ** 2 + (cols - cc) ** 2) / (2 * sigma**2)))


# ── mbsp calibration + refit ──


def test_mbsp_refit_recovers_c_that_plumeless_fit_biases() -> None:
    rng = np.random.default_rng(0)
    shape = (48, 48)
    c_true = 1.05
    r12_base = rng.uniform(0.10, 0.30, shape)
    r11 = c_true * r12_base + rng.normal(0.0, 1e-4, shape)
    # A methane plume depresses B12 in one corner (absorption), not B11.
    plume = _gauss(shape, 10, 10, 4.0)
    r12 = r12_base * (1.0 - 0.15 * plume)

    result = mbsp(r11, r12)
    # The naive fit is biased by the plume; the refit recovers c_true to <1%.
    assert abs(result.c_initial - c_true) > 0.005
    assert abs(result.c - c_true) < 0.01 * c_true
    assert result.n_excluded > 0
    # ΔR is negative inside the plume (stronger B12 absorption).
    assert result.delta_r[10, 10] < -0.02


def test_mbsp_nan_propagation() -> None:
    rng = np.random.default_rng(1)
    r12 = rng.uniform(0.1, 0.3, (16, 16))
    r11 = 1.02 * r12
    r11[0, 0] = np.nan
    r12[1, 1] = np.nan
    out = mbsp(r11, r12)
    assert np.isnan(out.delta_r[0, 0])
    assert np.isnan(out.delta_r[1, 1])
    assert np.isfinite(out.c)


def test_mbsp_zero_r11_is_nan_not_inf() -> None:
    rng = np.random.default_rng(3)
    r12 = rng.uniform(0.15, 0.25, (8, 8))
    r11 = 1.0 * r12
    r11[0, 0] = 0.0  # a zero-reflectance pixel must yield NaN, not inf
    out = mbsp(r11, r12)
    assert np.isnan(out.delta_r[0, 0])
    assert np.isfinite(out.delta_r[4, 4])


# ── mbmp cancels shared surface structure ──


def test_mbmp_cancels_shared_structure() -> None:
    rng = np.random.default_rng(2)
    shape = (40, 40)
    # A static surface pattern present identically in both passes' ΔR, plus
    # independent per-pass noise. MBMP subtracts the shared structure.
    surface = 0.05 * _gauss(shape, 20, 20, 8.0)
    r12 = rng.uniform(0.1, 0.3, shape)

    def pass_with(seed: int) -> np.ndarray:
        noise = np.random.default_rng(seed).normal(0.0, 2e-4, shape)
        r11 = (1.0 + surface) * r12 + noise  # surface imprints on ΔR identically
        return mbsp(r11, r12).delta_r

    dr_t = pass_with(10)
    dr_r = pass_with(11)

    def _result(dr: np.ndarray) -> MbspResult:
        return MbspResult(delta_r=dr, c=1.0, c_initial=1.0, n_excluded=0)

    residual = mbmp(_result(dr_t), _result(dr_r))
    assert np.nanstd(residual) < np.nanstd(dr_t)


# ── fetch_chip guards + fill conversion ──


def test_fetch_chip_refuses_oversized_grid() -> None:
    from datetime import UTC, datetime

    from openearth.methane.scenes import S2Scene

    scene = S2Scene("x", datetime(2018, 6, 19, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0)
    # A ~1° box at 20 m is far larger than 1024²; refused before any EE call.
    with pytest.raises(ValueError, match="Refusing"):
        fetch_chip(scene, BBox(53.7, 38.2, 54.7, 38.8), scale_m=20)


def test_fill_to_reflectance_maps_sentinel_to_nan() -> None:
    cube = np.zeros((3, 3, len(CHIP_BANDS)), dtype=np.float32)
    cube[...] = 2000.0  # DN → 0.2 reflectance
    cube[0, 0, :] = _FILL  # a masked pixel
    bands = _fill_to_reflectance(cube, CHIP_BANDS)
    assert set(bands) == set(CHIP_BANDS)
    assert np.isnan(bands["B12"][0, 0])
    assert bands["B11"][1, 1] == pytest.approx(0.2)
    # Reflectances land in the expected physical band.
    finite = bands["B04"][np.isfinite(bands["B04"])]
    assert np.all((finite >= 0.0) & (finite < 1.5))
