"""Stage 5 — IME + Monte-Carlo quantification (offline)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from openearth.ee.pixels import GridSpec, grid_for
from openearth.geometry import BBox
from openearth.methane import ime as ime_mod
from openearth.methane.ime import (
    McParams,
    emission_over_mask,
    ime_kg,
    plume_length_m,
    quantify,
    u_eff_ms,
)
from openearth.methane.plume import detect_plume, pixel_area_m2
from openearth.methane.wind import WindSample


def _grid(shape: tuple[int, int], lat: float = 38.0) -> GridSpec:
    h, w = shape
    bbox = BBox(54.0, lat - 0.005, 54.006, lat + 0.005)
    g = grid_for(bbox, 20)
    return GridSpec(x0=g.x0, y0=g.y0, xscale=g.xscale, yscale=g.yscale, width=w, height=h)


def _wind(speed: float) -> WindSample:
    # u = speed (pure easterly) so speed_ms == speed exactly.
    return WindSample.from_uv(datetime(2018, 6, 19, 7, 30, tzinfo=UTC), speed, 0.0, "test")


def _gauss_field(shape: tuple[int, int], amp: float, sigma: float) -> np.ndarray:
    rows, cols = np.indices(shape)
    cr, cc = shape[0] / 2, shape[1] / 2
    return amp * np.exp(-(((rows - cr) ** 2 + (cols - cc) ** 2) / (2 * sigma**2)))


# ── primitives ──


def test_ime_kg_hand_computed() -> None:
    grid = _grid((3, 3))
    field = np.zeros((3, 3))
    mask = np.zeros((3, 3), dtype=bool)
    field[1, 1] = field[1, 0] = field[0, 1] = 0.5  # 3-px mask, ΔΩ = 0.5 each
    mask[1, 1] = mask[1, 0] = mask[0, 1] = True
    a_pix = pixel_area_m2(grid)
    expected = 3 * 0.5 * a_pix * 0.01604
    assert ime_kg(field, mask, grid) == pytest.approx(expected)


def test_ime_kg_nan_in_mask_contributes_zero() -> None:
    grid = _grid((3, 3))
    field = np.array([[np.nan, 0.5, 0.0], [0.5, 0.0, 0.0], [0.0, 0.0, 0.0]])
    mask = np.zeros((3, 3), dtype=bool)
    mask[0, 0] = mask[0, 1] = mask[1, 0] = True  # includes the NaN pixel
    a_pix = pixel_area_m2(grid)
    assert ime_kg(field, mask, grid) == pytest.approx(2 * 0.5 * a_pix * 0.01604)


def test_plume_length_hand() -> None:
    grid = _grid((5, 5))
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = mask[0, 1] = mask[1, 0] = True  # 3 px
    assert plume_length_m(mask, grid) == pytest.approx(np.sqrt(3 * pixel_area_m2(grid)))


def test_u_eff() -> None:
    assert u_eff_ms(3.2) == pytest.approx(1.506)


# ── quantify: deterministic golden path ──


def test_quantify_deterministic_matches_closed_form(monkeypatch: pytest.MonkeyPatch) -> None:
    # With every noise term off (model σ = 0, σ_u10 = 0, single k) the MC median Q must
    # equal the closed-form Q over the display mask. Median-centring (fix 4a) means a
    # uniform block is the plume only against a zero-*median* background, so we use a tiny
    # ±b checkerboard background (median 0, σ > 0) whose off-plume bootstrap contribution
    # is ~b·√n_px — utterly negligible next to the unit-valued plume.
    monkeypatch.setattr(ime_mod, "IME_MODEL_SIGMA_FRAC", 0.0)
    shape = (60, 60)
    grid = _grid(shape)
    b = 1e-9
    rows, cols = np.indices(shape)
    field = np.where((rows + cols) % 2 == 0, b, -b).astype(float)  # median 0, σ = 1.4826·b
    field[20:40, 20:40] = 1.0  # a solid 400-px plume, no internal spread
    wind = _wind(4.0)

    est, mask = quantify(
        field, grid, wind, sigma_u10=0.0, k_sigma=2.0, mc=McParams(n=1, k_grid=(2.0,))
    )
    pm = detect_plume(field, grid, k_sigma=2.0)
    assert pm.n_pixels == 400  # the whole solid block clears median + k·σ
    q0 = u_eff_ms(4.0) / plume_length_m(pm.mask, grid) * ime_kg(field, pm.mask, grid) * 3600.0
    assert est.q_kg_h == pytest.approx(q0, rel=1e-6)
    assert mask.n_pixels == pm.n_pixels


def test_quantify_full_mc_median_near_deterministic() -> None:
    shape = (60, 60)
    grid = _grid(shape)
    field = _gauss_field(shape, amp=1.0, sigma=6.0)
    wind = _wind(4.0)

    est, _ = quantify(field, grid, wind, sigma_u10=1.5, mc=McParams(n=500, seed=7))
    pm = detect_plume(field, grid, k_sigma=2.0)
    q0 = u_eff_ms(4.0) / plume_length_m(pm.mask, grid) * ime_kg(field, pm.mask, grid) * 3600.0
    assert est.q_kg_h == pytest.approx(q0, rel=0.15)
    # Percentiles monotone.
    p = est.percentiles
    assert p["p05"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]
    # Histogram: 25 edges, 24 counts summing to n.
    assert len(est.histogram["edges"]) == 25
    assert len(est.histogram["counts"]) == 24
    assert sum(est.histogram["counts"]) == 500


def test_quantify_is_deterministic_for_fixed_seed() -> None:
    shape = (50, 50)
    grid = _grid(shape)
    field = _gauss_field(shape, amp=1.0, sigma=5.0)
    wind = _wind(4.0)
    a, _ = quantify(field, grid, wind, sigma_u10=1.5, mc=McParams(n=300, seed=42))
    b, _ = quantify(field, grid, wind, sigma_u10=1.5, mc=McParams(n=300, seed=42))
    assert a.q_kg_h == b.q_kg_h
    assert a.q_sigma_kg_h == b.q_sigma_kg_h


def test_quantify_u10_truncated_at_floor() -> None:
    # Huge σ_u10 with a small mean would push draws negative; they must clip ≥ 0.1.
    shape = (50, 50)
    grid = _grid(shape)
    field = _gauss_field(shape, amp=1.0, sigma=5.0)
    wind = _wind(0.5)
    est, _ = quantify(field, grid, wind, sigma_u10=10.0, mc=McParams(n=400, seed=1))
    # A negative u10 would make u_eff (hence Q) negative; the floor keeps Q > 0.
    assert est.percentiles["p05"] > 0.0


def test_quantify_no_plume_returns_nan_estimate() -> None:
    shape = (40, 40)
    grid = _grid(shape)
    field = np.random.default_rng(0).normal(0.0, 1e-3, shape)  # no plume
    # Add isolated salt so detect_plume's opening yields an empty mask.
    field[5, 5] = 1.0
    est, mask = quantify(field, grid, _wind(4.0), sigma_u10=1.5)
    assert mask.n_pixels == 0
    assert np.isnan(est.q_kg_h)
    assert est.ime_kg == 0.0


def test_emission_over_mask_single_pass_matches_formula() -> None:
    grid = _grid((30, 30))
    delta_omega = _gauss_field((30, 30), amp=0.05, sigma=4.0)
    mask = delta_omega > 0.02
    est = emission_over_mask(delta_omega, grid, mask, _wind(4.0), sigma_u10=1.5)
    ime = ime_kg(delta_omega, mask, grid)
    length = plume_length_m(mask, grid)
    expected = u_eff_ms(4.0) / length * ime * 3600.0
    assert est.q_kg_h == pytest.approx(expected, rel=1e-9)
    assert np.isnan(est.q_sigma_kg_h)  # single-pass: no MC budget
    assert est.n_mc == 0


def test_emission_over_mask_empty_mask_is_nan() -> None:
    grid = _grid((16, 16))
    est = emission_over_mask(np.zeros((16, 16)), grid, np.zeros((16, 16), bool), _wind(3.0), 1.0)
    assert np.isnan(est.q_kg_h)
    assert est.ime_kg == 0.0
