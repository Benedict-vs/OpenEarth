"""Stage 1 — CH4 LUT + conversion tests, all against the committed npz."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from openearth.methane import conversion
from openearth.methane.constants import OMEGA_CH4_BACKGROUND_MOL_M2

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Anchor values of the committed ch4_lut_v3.npz at Varon's geometry (AMF ≈ 2.305,
# ΔΩ = 0.65 mol/m²) — our own layered-model reference for regression pinning.
V3_ANCHOR_M_S2A = -0.036307
V3_ANCHOR_M_S2B = -0.027335


@pytest.fixture(scope="module")
def lut() -> conversion.CH4Lut:
    return conversion.load_lut()


# ── Structure ──


def test_lut_structure(lut: conversion.CH4Lut) -> None:
    assert lut.version == "3"
    assert lut.delta_omega.shape == (351,)
    assert lut.amf.shape == (9,)
    for name in ("Sentinel-2A", "Sentinel-2B"):
        assert lut.m[name].shape == (9, 351)
        assert np.isfinite(lut.m[name]).all()
    # Grid endpoints as pinned in the plan (ΔΩ top raised to 3.0 in v3 so
    # saturated super-emitter cores don't clip at the grid end).
    assert lut.delta_omega[0] == pytest.approx(-0.5)
    assert lut.delta_omega[-1] == pytest.approx(3.0)
    assert lut.amf[0] == pytest.approx(2.0)
    assert lut.amf[-1] == pytest.approx(4.0)


def test_provenance_parses(lut: conversion.CH4Lut) -> None:
    prov = json.loads(lut.provenance)
    assert "hitran_fetch_date" in prov
    assert prov["omega_background_mol_m2"] == pytest.approx(OMEGA_CH4_BACKGROUND_MOL_M2)
    assert prov["hitran_isotopologue_global_ids"]
    # v3 is a layered model: US Std Atmosphere background in equal-mass layers,
    # enhancement in the lowest 500 m (Varon et al. 2021 placement).
    assert prov["n_layers"] >= 10
    assert len(prov["layer_pressure_atm"]) == prov["n_layers"]
    assert sum(prov["layer_mass_fractions"]) == pytest.approx(1.0, abs=1e-4)
    assert prov["enhancement_layer"]["top_m"] == pytest.approx(500.0)
    assert prov["enhancement_layer"]["pressure_atm"] == pytest.approx(0.971, abs=0.005)
    assert "layered" in prov["model"]


def test_load_lut_is_cached(lut: conversion.CH4Lut) -> None:
    assert conversion.load_lut() is lut


# ── Curve shape ──


def test_m_zero_at_zero_enhancement(lut: conversion.CH4Lut) -> None:
    j0 = int(np.argmin(np.abs(lut.delta_omega)))
    assert lut.delta_omega[j0] == pytest.approx(0.0)
    for name in ("Sentinel-2A", "Sentinel-2B"):
        assert np.allclose(lut.m[name][:, j0], 0.0, atol=1e-12)


def test_m_strictly_decreasing_in_delta_omega(lut: conversion.CH4Lut) -> None:
    for name in ("Sentinel-2A", "Sentinel-2B"):
        for row in lut.m[name]:
            assert np.all(np.diff(row) < 0)


def test_abs_m_increasing_in_amf(lut: conversion.CH4Lut) -> None:
    # At a fixed strong enhancement, deeper slant path ⇒ larger |signal|.
    j = int(np.argmin(np.abs(lut.delta_omega - 1.0)))
    for name in ("Sentinel-2A", "Sentinel-2B"):
        col = np.abs(lut.m[name][:, j])
        assert np.all(np.diff(col) > 0)


# ── Anchor (Varon et al. 2021, Sect. 2) ──


def _anchor_signals(lut: conversion.CH4Lut) -> tuple[float, float]:
    amf = 1.0 / np.cos(np.radians(40.0)) + 1.0  # ≈ 2.305 (VZA 0°, SZA 40°)
    delta_omega = OMEGA_CH4_BACKGROUND_MOL_M2  # doubled background

    def m_mbsp(sat: str) -> float:
        do, m = conversion.forward_signal(lut, sat, amf)
        return float(np.interp(delta_omega, do, m))

    return m_mbsp("Sentinel-2A"), m_mbsp("Sentinel-2B")


def test_varon_anchor(lut: conversion.CH4Lut) -> None:
    # Deliberately a *loose sanity band*, not a precision target: Varon's
    # reference model differs structurally from ours (interfering H2O/CO2,
    # solar-spectrum radiance weighting), so closer agreement with this one
    # published point can come from error cancellation and must not be
    # test-enforced. Correctness is pinned against our own layered reference
    # in test_v3_regression_pin instead.
    m_a, m_b = _anchor_signals(lut)
    assert m_a == pytest.approx(-0.029, rel=0.30)
    assert m_b == pytest.approx(-0.022, rel=0.30)
    assert abs(m_a) > abs(m_b)


def test_v3_regression_pin(lut: conversion.CH4Lut) -> None:
    # Regression pin against the committed v3 LUT's own anchor values (layered
    # US Std Atmosphere background + 500 m enhancement slab). A regenerated LUT
    # that moves these by > 1 % is a physics change and must bump the version.
    m_a, m_b = _anchor_signals(lut)
    assert m_a == pytest.approx(V3_ANCHOR_M_S2A, rel=0.01)
    assert m_b == pytest.approx(V3_ANCHOR_M_S2B, rel=0.01)


# ── Inversion round-trip ──


def test_forward_invert_round_trip(lut: conversion.CH4Lut) -> None:
    for sat in ("Sentinel-2A", "Sentinel-2B"):
        do, m = conversion.forward_signal(lut, sat, 2.5)
        recovered = conversion.invert_fractional_signal(m, lut, sat, 2.5)
        # Interior only (the ends clip by design).
        assert np.allclose(recovered[5:-5], do[5:-5], atol=1e-3)


def test_invert_nan_passthrough(lut: conversion.CH4Lut) -> None:
    out = conversion.invert_fractional_signal(np.array([np.nan, -0.01]), lut, "Sentinel-2A", 2.3)
    assert np.isnan(out[0])
    assert np.isfinite(out[1])


def test_invert_out_of_range_clips(lut: conversion.CH4Lut) -> None:
    # m far below/above the tabulated range clip to the ΔΩ grid ends without raising.
    out = conversion.invert_fractional_signal(np.array([-1.0, 1.0]), lut, "Sentinel-2A", 2.3)
    assert out[0] == pytest.approx(lut.delta_omega[-1])  # very negative m ⇒ max ΔΩ
    assert out[1] == pytest.approx(lut.delta_omega[0])  # positive m ⇒ min ΔΩ


def test_forward_amf_clamps(lut: conversion.CH4Lut) -> None:
    _, low = conversion.forward_signal(lut, "Sentinel-2A", 1.0)  # below grid
    _, edge = conversion.forward_signal(lut, "Sentinel-2A", 2.0)
    assert np.allclose(low, edge)


# ── ΔΩ → ΔXCH4 ──


def test_delta_omega_to_xch4_scalar() -> None:
    assert conversion.delta_omega_to_xch4_ppb(0.65) == pytest.approx(1822.0, rel=1e-3)


def test_delta_omega_to_xch4_array() -> None:
    out = conversion.delta_omega_to_xch4_ppb(np.array([0.0, 0.65]))
    assert isinstance(out, np.ndarray)
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(1822.0, rel=1e-3)


# ── Generator helpers (pure: analytic top-hat identity + US Std Atmosphere) ──


def _load_generator():  # type: ignore[no-untyped-def]
    path = _REPO_ROOT / "scripts" / "generate_ch4_lut.py"
    spec = importlib.util.spec_from_file_location("generate_ch4_lut", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_band_fractional_signal_top_hat_analytic() -> None:
    # With a top-hat SRF and constant k the background weighting cancels and
    # m = exp(−AMF·ΔΩ·k) − 1 exactly, for ANY background optical depth.
    gen = _load_generator()
    nu = np.linspace(4000.0, 4100.0, 2000)
    srf = np.ones_like(nu)
    k = np.full_like(nu, 1e-21 * gen.AVOGADRO_PER_MOL * 1e-4)  # m²/mol
    tau_bg = 0.65 * 0.7 * k  # arbitrary constant background optical depth
    delta_omega, amf = 0.5, 2.3
    m = gen.band_fractional_signal(nu, srf, tau_bg, k, delta_omega, amf)
    expected = np.exp(-amf * delta_omega * 1e-21 * gen.AVOGADRO_PER_MOL * 1e-4) - 1.0
    assert m == pytest.approx(expected, rel=1e-6)


def test_us_standard_profile_matches_ussa_tables() -> None:
    gen = _load_generator()
    t, p = gen.us_standard_profile(np.array([0.0, 11_000.0, 20_000.0, 32_000.0, 47_000.0]))
    assert np.allclose(t, [288.15, 216.65, 216.65, 228.65, 270.65])
    # USSA 1976 tabulated pressures (hPa) at the layer bases.
    assert np.allclose(p / 100.0, [1013.25, 226.32, 54.75, 8.68, 1.11], rtol=1e-3)


def test_equal_mass_layers_recover_column_means() -> None:
    # The layered discretisation must reproduce the analytic column integrals:
    # absorber-weighted mean pressure P0/2 and full-column mass-weighted T.
    gen = _load_generator()
    layers = gen.equal_mass_layers(gen.N_LAYERS)
    fracs = np.array([f for f, _, _ in layers])
    p_eff = np.array([p for _, p, _ in layers])
    t_eff = np.array([t for _, _, t in layers])
    assert fracs.sum() == pytest.approx(1.0)
    assert float(fracs @ p_eff) == pytest.approx(0.5005, abs=0.002)
    assert float(fracs @ t_eff) == pytest.approx(250.2, abs=0.5)


def test_enhancement_slab_conditions() -> None:
    # Varon et al. 2021 place the plume in the lowest 500 m; its
    # absorber-weighted conditions are near-surface, NOT column-mean.
    gen = _load_generator()
    p_atm, t_k = gen.slab_conditions(gen.ENHANCEMENT_TOP_M)
    assert p_atm == pytest.approx(0.971, abs=0.002)
    assert t_k == pytest.approx(286.5, abs=0.5)
