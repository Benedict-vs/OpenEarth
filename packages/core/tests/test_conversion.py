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


@pytest.fixture(scope="module")
def lut() -> conversion.CH4Lut:
    return conversion.load_lut()


# ── Structure ──


def test_lut_structure(lut: conversion.CH4Lut) -> None:
    assert lut.version == "1"
    assert lut.delta_omega.shape == (251,)
    assert lut.amf.shape == (9,)
    for name in ("Sentinel-2A", "Sentinel-2B"):
        assert lut.m[name].shape == (9, 251)
        assert np.isfinite(lut.m[name]).all()
    # Grid endpoints as pinned in the plan.
    assert lut.delta_omega[0] == pytest.approx(-0.5)
    assert lut.delta_omega[-1] == pytest.approx(2.0)
    assert lut.amf[0] == pytest.approx(2.0)
    assert lut.amf[-1] == pytest.approx(4.0)


def test_provenance_parses(lut: conversion.CH4Lut) -> None:
    prov = json.loads(lut.provenance)
    assert "hitran_fetch_date" in prov
    assert prov["omega_background_mol_m2"] == pytest.approx(OMEGA_CH4_BACKGROUND_MOL_M2)
    assert prov["hitran_isotopologue_global_ids"]


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


def test_varon_anchor(lut: conversion.CH4Lut) -> None:
    amf = 1.0 / np.cos(np.radians(40.0)) + 1.0  # ≈ 2.305
    delta_omega = OMEGA_CH4_BACKGROUND_MOL_M2  # doubled background

    def m_mbsp(sat: str) -> float:
        do, m = conversion.forward_signal(lut, sat, amf)
        return float(np.interp(delta_omega, do, m))

    m_a = m_mbsp("Sentinel-2A")
    m_b = m_mbsp("Sentinel-2B")
    assert m_a == pytest.approx(-0.029, rel=0.30)
    assert m_b == pytest.approx(-0.022, rel=0.30)
    assert abs(m_a) > abs(m_b)


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


# ── Generator band helper (pure, analytic top-hat identity) ──


def _load_generator():  # type: ignore[no-untyped-def]
    path = _REPO_ROOT / "scripts" / "generate_ch4_lut.py"
    spec = importlib.util.spec_from_file_location("generate_ch4_lut", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_band_fractional_signal_top_hat_analytic() -> None:
    gen = _load_generator()
    nu = np.linspace(4000.0, 4100.0, 2000)
    srf = np.ones_like(nu)
    sigma = np.full_like(nu, 1e-21)  # constant cross section, cm²/molecule
    omega0, delta_omega, amf = 0.65, 0.5, 2.3
    m = gen.band_fractional_signal(nu, srf, sigma, omega0, delta_omega, amf)
    expected = np.exp(-amf * delta_omega * gen.AVOGADRO_PER_MOL * 1e-4 * 1e-21) - 1.0
    assert m == pytest.approx(expected, rel=1e-6)
