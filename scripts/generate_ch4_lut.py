#!/usr/bin/env python
"""Generate the committed CH4 absorption LUT (``ch4_lut_v3.npz``).

Run manually, once, with network access (HITRAN line tables are cached in the
scratch directory and re-used offline on later runs):

    uv run --group lut python scripts/generate_ch4_lut.py

The runtime library never imports this script or HAPI — it loads only the
committed ``.npz`` via :mod:`openearth.methane.conversion`. HITRAN line tables
are fetched into a scratch directory and are **not** committed; the Sentinel-2
SRF extract (``scripts/data/s2_srf_b11_b12.csv``) and the resulting ``.npz``
are.

Physics (layered Beer–Lambert band transmittance, no scattering):

  The background column Ω0 is distributed over the US Standard Atmosphere 1976
  as ``N_LAYERS`` equal-mass layers (well-mixed CH4 ⇒ absorber fraction =
  pressure fraction), each with its own absorber-weighted (T_i, p_i) and its
  own HITRAN Voigt cross section σ_i(ν). The plume enhancement ΔΩ sits in the
  lowest ``ENHANCEMENT_TOP_M`` of the atmosphere at that slab's own
  absorber-weighted conditions — the vertical placement Varon et al. 2021
  (AMT 14:2771) assume in their 100-layer reference model. This replaces the
  v1/v2 single-effective-layer collapse, where one (T, p) was applied to both
  background *and* enhancement: narrow 0.5-atm plume lines coincide exactly
  with the background-saturated cores and understate the marginal absorption,
  while surface-pressure background lines overstate the band absorption.

    τ_bg(ν; AMF)   = AMF · Ω0 · Σ_i f_i k_i(ν)      [k = N_A·1e-4·σ, per mol/m²]
    w(ν; AMF)      = SRF_b(ν) · e^{−τ_bg}
    m_b(ΔΩ, AMF)   = ∫ w e^{−AMF·ΔΩ·k_enh} dν / ∫ w dν − 1
    m_MBSP(ΔΩ)     = (1 + m_B12) / (1 + m_B11) − 1

computed separately for Sentinel-2A and Sentinel-2B (their B12 SRFs differ
enough to matter). HAPI is imported lazily so the pure profile + band helpers
below can be unit-tested without it.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

# Kept in lockstep with openearth.methane.constants (which the script must not
# depend on structurally — it may run in an environment without the package).
AVOGADRO_PER_MOL = 6.02214076e23
OMEGA_CH4_BACKGROUND_MOL_M2 = 0.65

# HITRAN molecule 6 = CH4; global isotopologue ids for the main isotopologues
# (12CH4, 13CH4, CH3D, and 13CH3D). See hitran.org isotopologue metadata.
CH4_ISO_GLOBAL_IDS = [32, 33, 34, 35]

# SRF-supported band ranges in wavenumber (cm⁻¹) with a ±50 cm⁻¹ margin so the
# Voigt wings are captured. B11 ≈ 5946–6497, B12 ≈ 4310–4812.
BAND_NU_RANGES = {"B11": (5896.0, 6547.0), "B12": (4260.0, 4862.0)}

WAVENUMBER_STEP = 0.005  # cm⁻¹

# ── Vertical discretisation ──
# The well-mixed background is split into equal-mass layers of the US Standard
# Atmosphere 1976 (each layer's absorber-weighted T/p feeds its own Voigt cross
# section); the enhancement is a near-surface slab per Varon et al. 2021 ("the
# methane enhancement is presumed to be in the lowest 500 m of the atmosphere").
N_LAYERS = 16
ENHANCEMENT_TOP_M = 500.0

# US Standard Atmosphere 1976: (base altitude m, base temperature K, lapse K/m)
# up to 47 km. The ~0.1 % of column mass above 47 km is folded into the top
# layer's weight by normalising the layer fractions to 1.
_USSA_BASES = (
    (0.0, 288.15, -6.5e-3),
    (11_000.0, 216.65, 0.0),
    (20_000.0, 216.65, 1.0e-3),
    (32_000.0, 228.65, 2.8e-3),
)
_USSA_TOP_M = 47_000.0
P0_PA = 101_325.0
_G0 = 9.80665  # m/s²
_R_AIR = 287.053  # J/(kg·K)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRF_CSV = REPO_ROOT / "scripts" / "data" / "s2_srf_b11_b12.csv"
LUT_OUT = (
    REPO_ROOT / "packages" / "core" / "src" / "openearth" / "methane" / "data" / "ch4_lut_v3.npz"
)

# The ΔΩ × AMF grid the runtime interpolates over. The ΔΩ top end is 3.0 (v2:
# 2.0) so saturated super-emitter cores don't silently clip at the grid end.
DELTA_OMEGA_GRID = np.linspace(-0.5, 3.0, 351)  # mol/m²
AMF_GRID = np.round(np.arange(2.0, 4.0 + 1e-9, 0.25), 4)  # 9 points, 2.0…4.0


# ── US Standard Atmosphere 1976 (pure; unit-tested offline) ──


def us_standard_profile(
    z_m: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Temperature (K) and pressure (Pa) of the US Std Atmosphere 1976 at *z_m*.

    Analytic piecewise-lapse hydrostatic profile, valid for 0 ≤ z ≤ 47 km.
    """
    t = np.empty_like(z_m)
    p = np.empty_like(z_m)
    p_base = P0_PA
    for i, (z_base, t_base, lapse) in enumerate(_USSA_BASES):
        z_top = _USSA_BASES[i + 1][0] if i + 1 < len(_USSA_BASES) else _USSA_TOP_M
        mask = (z_m >= z_base) & (z_m <= z_top)
        dz = z_m[mask] - z_base
        if lapse == 0.0:
            t[mask] = t_base
            p[mask] = p_base * np.exp(-_G0 * dz / (_R_AIR * t_base))
        else:
            t[mask] = t_base + lapse * dz
            p[mask] = p_base * (t[mask] / t_base) ** (-_G0 / (_R_AIR * lapse))
        # Advance the hydrostatic base to the top of this lapse segment.
        dz_full = z_top - z_base
        t_top = t_base + lapse * dz_full
        if lapse == 0.0:
            p_base *= float(np.exp(-_G0 * dz_full / (_R_AIR * t_base)))
        else:
            p_base *= float((t_top / t_base) ** (-_G0 / (_R_AIR * lapse)))
    return t, p


def _fine_profile() -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Fine-grained (z, T, p) sampling used for the absorber-weighted means."""
    z = np.linspace(0.0, _USSA_TOP_M, 47_001)
    t, p = us_standard_profile(z)
    return z, t, p


def equal_mass_layers(n_layers: int) -> list[tuple[float, float, float]]:
    """Split the column into *n_layers* equal-mass slabs.

    Returns ``[(mass_fraction, p_eff_atm, t_eff_k), …]`` from the surface up,
    with per-slab absorber-weighted (Curtis–Godson) mean pressure/temperature.
    For a well-mixed absorber the mass fraction is the absorber fraction; the
    fractions are normalised to sum to 1 so Ω0 is preserved exactly despite
    the 47 km truncation.
    """
    _, t, p = _fine_profile()
    dp = -np.diff(p)
    p_mid = 0.5 * (p[1:] + p[:-1])
    t_mid = 0.5 * (t[1:] + t[:-1])
    edges = np.linspace(P0_PA, float(p[-1]), n_layers + 1)  # decreasing pressure edges
    out: list[tuple[float, float, float]] = []
    total = float(dp.sum())
    for i in range(n_layers):
        mask = (p_mid <= edges[i]) & (p_mid > edges[i + 1])
        w = dp[mask]
        frac = float(w.sum()) / total
        p_eff = float((p_mid[mask] * w).sum() / w.sum()) / P0_PA  # atm (P0 ≡ 1 atm here)
        t_eff = float((t_mid[mask] * w).sum() / w.sum())
        out.append((frac, p_eff, t_eff))
    return out


def slab_conditions(z_top_m: float) -> tuple[float, float]:
    """Absorber-weighted (p_atm, T_K) of the slab from the surface to *z_top_m*."""
    z, t, p = _fine_profile()
    dp = -np.diff(p)
    p_mid = 0.5 * (p[1:] + p[:-1])
    t_mid = 0.5 * (t[1:] + t[:-1])
    mask = 0.5 * (z[1:] + z[:-1]) < z_top_m
    w = dp[mask]
    return float((p_mid[mask] * w).sum() / w.sum()) / P0_PA, float(
        (t_mid[mask] * w).sum() / w.sum()
    )


# ── Pure band physics (unit-tested with a synthetic top-hat SRF + constant k) ──


def band_fractional_signals(
    nu: NDArray[np.float64],
    srf: NDArray[np.float64],
    tau_bg_vert: NDArray[np.float64],
    k_enh: NDArray[np.float64],
    delta_omegas: NDArray[np.float64],
    amf: float,
) -> NDArray[np.float64]:
    """Per-band fractional signals m_b(ΔΩ) = T_b(Ω0+ΔΩ)/T_b(Ω0) − 1.

    ``tau_bg_vert`` is the *vertical* background optical depth Ω0·Σ f_i k_i(ν);
    ``k_enh`` the enhancement-slab absorption per unit column (m²/mol,
    = N_A·1e-4·σ_enh). The background weighting means the enhancement only
    produces signal where the background hasn't already saturated the band.
    """
    w = srf * np.exp(-amf * tau_bg_vert)
    den = float(np.trapezoid(w, nu))
    out = np.empty_like(delta_omegas)
    for j, d_omega in enumerate(delta_omegas):
        num = float(np.trapezoid(w * np.exp(-amf * d_omega * k_enh), nu))
        out[j] = num / den - 1.0
    return out


def band_fractional_signal(
    nu: NDArray[np.float64],
    srf: NDArray[np.float64],
    tau_bg_vert: NDArray[np.float64],
    k_enh: NDArray[np.float64],
    delta_omega: float,
    amf: float,
) -> float:
    """Scalar wrapper over :func:`band_fractional_signals`.

    With a top-hat SRF and constant k this reduces analytically to
    ``exp(−AMF·ΔΩ·k) − 1`` for any background τ (the anchor for the unit test).
    """
    return float(
        band_fractional_signals(nu, srf, tau_bg_vert, k_enh, np.array([delta_omega]), amf)[0]
    )


def mbsp_fractional_signal(m_b11: float, m_b12: float) -> float:
    """Combine the two per-band signals into the MBSP fractional signal."""
    return (1.0 + m_b12) / (1.0 + m_b11) - 1.0


# ── SRF loading + resampling (pure) ──


def load_srf_csv(path: Path) -> dict[str, NDArray[np.float64]]:
    """Load the committed B11/B12 SRF extract into named float arrays."""
    columns: dict[str, list[float]] = {}
    with path.open() as f:
        reader = csv.DictReader(row for row in f if not row.startswith("#"))
        assert reader.fieldnames is not None
        for name in reader.fieldnames:
            columns[name] = []
        for row in reader:
            for name in reader.fieldnames:
                columns[name].append(float(row[name]))
    return {name: np.asarray(vals, dtype=np.float64) for name, vals in columns.items()}


def srf_on_nu_grid(
    nu_grid: NDArray[np.float64],
    wavelength_nm: NDArray[np.float64],
    srf_col: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Resample an SRF from wavelength (nm) onto an increasing wavenumber grid.

    ν [cm⁻¹] = 1e7 / λ [nm]. Response outside the tabulated support is 0.
    """
    nu_src = 1e7 / wavelength_nm
    order = np.argsort(nu_src)
    nu_sorted = nu_src[order]
    srf_sorted = srf_col[order]
    return np.asarray(
        np.interp(nu_grid, nu_sorted, srf_sorted, left=0.0, right=0.0), dtype=np.float64
    )


# ── HAPI cross sections (network on first run; lazy import) ──


def compute_layer_cross_sections(
    scratch_dir: Path,
    layers: list[tuple[float, float, float]],
    enh_conditions: tuple[float, float],
) -> dict[str, tuple[NDArray[np.float64], list[NDArray[np.float64]], NDArray[np.float64]]]:
    """Voigt cross sections per band: one σ_i per background layer + σ_enh.

    Returns ``{band: (nu, [sigma_layer, …], sigma_enh)}`` with σ in
    cm²/molecule. Cached HITRAN tables in *scratch_dir* are re-used; fetching
    happens only when a band's table is missing (requires network).
    """
    import hapi

    scratch_dir.mkdir(parents=True, exist_ok=True)
    hapi.db_begin(str(scratch_dir))

    def sigma_at(
        table: str, t_k: float, p_atm: float
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        nu, coef = hapi.absorptionCoefficient_Voigt(
            SourceTables=table,
            Environment={"T": t_k, "p": p_atm},
            WavenumberStep=WAVENUMBER_STEP,
            HITRAN_units=True,  # σ in cm²/molecule
            Diluent={"air": 1.0},
        )
        return np.asarray(nu, dtype=np.float64), np.asarray(coef, dtype=np.float64)

    p_enh, t_enh = enh_conditions
    out: dict[str, tuple[NDArray[np.float64], list[NDArray[np.float64]], NDArray[np.float64]]] = {}
    for band, (nu_min, nu_max) in BAND_NU_RANGES.items():
        table = f"CH4_{band}"
        if not (scratch_dir / f"{table}.header").exists():
            try:
                hapi.fetch_by_ids(table, CH4_ISO_GLOBAL_IDS, nu_min, nu_max)
            except Exception:
                hapi.fetch(table, 6, 1, nu_min, nu_max)
        nu_ref: NDArray[np.float64] | None = None
        sigmas: list[NDArray[np.float64]] = []
        for _, p_atm, t_k in layers:
            nu, sigma = sigma_at(table, t_k, p_atm)
            if nu_ref is None:
                nu_ref = nu
            else:
                assert nu.shape == nu_ref.shape, "HAPI ν grids must match across layers"
            sigmas.append(sigma)
        assert nu_ref is not None
        _, sigma_enh = sigma_at(table, t_enh, p_enh)
        out[band] = (nu_ref, sigmas, sigma_enh)
    return out


# ── LUT assembly ──


def build_lut_arrays(
    cross_sections: dict[
        str, tuple[NDArray[np.float64], list[NDArray[np.float64]], NDArray[np.float64]]
    ],
    layers: list[tuple[float, float, float]],
    srf: dict[str, NDArray[np.float64]],
) -> dict[str, NDArray[np.float64]]:
    """Assemble the (M, N) MBSP fractional-signal grids for S2A and S2B."""
    wl = srf["wavelength_nm"]
    fracs = np.array([f for f, _, _ in layers])
    to_k = AVOGADRO_PER_MOL * 1e-4  # σ [cm²/molec] → k [m²/mol]

    per_band: dict[str, tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]] = {}
    for band, (nu, sigmas, sigma_enh) in cross_sections.items():
        k_layers = np.stack(sigmas) * to_k  # (L, nnu)
        tau_bg_vert = OMEGA_CH4_BACKGROUND_MOL_M2 * np.tensordot(fracs, k_layers, axes=1)
        per_band[band] = (nu, tau_bg_vert, sigma_enh * to_k)

    n = DELTA_OMEGA_GRID.size
    m = AMF_GRID.size
    out: dict[str, NDArray[np.float64]] = {}
    for key in ("s2a", "s2b"):
        nu11, tau11, k11 = per_band["B11"]
        nu12, tau12, k12 = per_band["B12"]
        srf11 = srf_on_nu_grid(nu11, wl, srf[f"{key}_b11"])
        srf12 = srf_on_nu_grid(nu12, wl, srf[f"{key}_b12"])
        grid = np.empty((m, n), dtype=np.float64)
        for i, amf in enumerate(AMF_GRID):
            m11 = band_fractional_signals(nu11, srf11, tau11, k11, DELTA_OMEGA_GRID, float(amf))
            m12 = band_fractional_signals(nu12, srf12, tau12, k12, DELTA_OMEGA_GRID, float(amf))
            grid[i] = (1.0 + m12) / (1.0 + m11) - 1.0
        out[key] = grid
    return out


def _git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch",
        type=Path,
        default=Path.home() / ".cache" / "openearth" / "hitran",
        help="Directory for HITRAN line tables (not committed).",
    )
    parser.add_argument("--out", type=Path, default=LUT_OUT)
    args = parser.parse_args()

    layers = equal_mass_layers(N_LAYERS)
    enh_p_atm, enh_t_k = slab_conditions(ENHANCEMENT_TOP_M)
    srf = load_srf_csv(SRF_CSV)
    cross_sections = compute_layer_cross_sections(args.scratch, layers, (enh_p_atm, enh_t_k))
    grids = build_lut_arrays(cross_sections, layers, srf)

    provenance = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "hitran_fetch_date": datetime.now(UTC).date().isoformat(),
        "hitran_molecule": "CH4 (6)",
        "hitran_isotopologue_global_ids": CH4_ISO_GLOBAL_IDS,
        "srf_document": (
            "ESA Sentinel-2 Spectral Response Functions, "
            "COPE-GSEG-EOPG-TN-15-0007 issue 3.2 (2022), via scripts/data/s2_srf_b11_b12.csv"
        ),
        "model": (
            "layered Beer-Lambert: well-mixed background over the US Standard "
            "Atmosphere 1976 in equal-mass layers (per-layer absorber-weighted T/p "
            "Voigt cross sections), enhancement in the lowest "
            f"{ENHANCEMENT_TOP_M:.0f} m at its absorber-weighted conditions "
            "(vertical placement per Varon et al. 2021). Replaces the v2 single "
            "effective-layer (Curtis-Godson) collapse, which applied one (T, p) to "
            "background and enhancement alike."
        ),
        "n_layers": N_LAYERS,
        "layer_mass_fractions": [round(f, 6) for f, _, _ in layers],
        "layer_pressure_atm": [round(p, 4) for _, p, _ in layers],
        "layer_temperature_k": [round(t, 2) for _, _, t in layers],
        "enhancement_layer": {
            "top_m": ENHANCEMENT_TOP_M,
            "pressure_atm": round(enh_p_atm, 4),
            "temperature_k": round(enh_t_k, 2),
        },
        "wavenumber_step_cm_inv": WAVENUMBER_STEP,
        "band_nu_ranges_cm_inv": BAND_NU_RANGES,
        "omega_background_mol_m2": OMEGA_CH4_BACKGROUND_MOL_M2,
        "script_git_hash": _git_hash(),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        delta_omega=DELTA_OMEGA_GRID,
        amf=AMF_GRID,
        m_s2a=grids["s2a"],
        m_s2b=grids["s2b"],
        version="3",
        provenance=json.dumps(provenance),
    )
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
