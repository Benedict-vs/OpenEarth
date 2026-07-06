#!/usr/bin/env python
"""Generate the committed CH4 absorption LUT (``ch4_lut_v1.npz``).

Run manually, once, with network access:

    uv run --group lut python scripts/generate_ch4_lut.py

The runtime library never imports this script or HAPI — it loads only the
committed ``.npz`` via :mod:`openearth.methane.conversion`. HITRAN line tables
are fetched into a scratch directory and are **not** committed; the Sentinel-2
SRF extract (``scripts/data/s2_srf_b11_b12.csv``) and the resulting ``.npz``
are.

Physics (Beer–Lambert band transmittance, no scattering):
  τ(ν; Ω, AMF) = Ω · AMF · N_A · 1e-4 · σ(ν)          [σ in cm²/molecule]
  T_b(Ω, AMF)  = ∫ SRF_b(ν) e^{-τ} dν / ∫ SRF_b(ν) dν
  m_b(ΔΩ)      = T_b(Ω0 + ΔΩ) / T_b(Ω0) − 1
  m_MBSP(ΔΩ)   = (1 + m_B12) / (1 + m_B11) − 1

computed separately for Sentinel-2A and Sentinel-2B (their B12 SRFs differ
enough to matter). HAPI and openpyxl are imported lazily so the pure band
helpers below can be unit-tested without them.
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
TEMPERATURE_K = 288.15
PRESSURE_ATM = 1.0

REPO_ROOT = Path(__file__).resolve().parent.parent
SRF_CSV = REPO_ROOT / "scripts" / "data" / "s2_srf_b11_b12.csv"
LUT_OUT = (
    REPO_ROOT / "packages" / "core" / "src" / "openearth" / "methane" / "data" / "ch4_lut_v1.npz"
)

# The ΔΩ × AMF grid the runtime interpolates over.
DELTA_OMEGA_GRID = np.linspace(-0.5, 2.0, 251)  # mol/m²
AMF_GRID = np.round(np.arange(2.0, 4.0 + 1e-9, 0.25), 4)  # 9 points, 2.0…4.0


# ── Pure band physics (unit-tested with a synthetic top-hat SRF + constant σ) ──


def band_transmittance(
    nu: NDArray[np.float64],
    srf: NDArray[np.float64],
    sigma: NDArray[np.float64],
    column_mol_m2: float,
    amf: float,
) -> float:
    """SRF-weighted band transmittance for a CH4 slant column.

    ``sigma`` is the absorption cross section in cm²/molecule on the ``nu``
    grid (increasing cm⁻¹); ``srf`` the (non-negative) spectral response on the
    same grid.
    """
    tau = column_mol_m2 * amf * AVOGADRO_PER_MOL * 1e-4 * sigma
    num = float(np.trapezoid(srf * np.exp(-tau), nu))
    den = float(np.trapezoid(srf, nu))
    return num / den


def band_fractional_signal(
    nu: NDArray[np.float64],
    srf: NDArray[np.float64],
    sigma: NDArray[np.float64],
    omega0: float,
    delta_omega: float,
    amf: float,
) -> float:
    """Per-band fractional signal m_b(ΔΩ) = T_b(Ω0+ΔΩ)/T_b(Ω0) − 1.

    With a top-hat SRF and constant σ this reduces analytically to
    ``exp(−AMF·ΔΩ·N_A·1e-4·σ) − 1`` (the anchor for the unit test).
    """
    t_bg = band_transmittance(nu, srf, sigma, omega0, amf)
    t_enh = band_transmittance(nu, srf, sigma, omega0 + delta_omega, amf)
    return t_enh / t_bg - 1.0


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


# ── HAPI cross sections (network; lazy import) ──


def compute_cross_sections(
    scratch_dir: Path,
) -> dict[str, tuple[NDArray[np.float64], NDArray[np.float64]]]:
    """Fetch CH4 lines and compute Voigt cross sections per band.

    Returns ``{band: (nu, sigma)}`` with σ in cm²/molecule at
    ``TEMPERATURE_K``/``PRESSURE_ATM``. Requires network on first run.
    """
    import hapi

    scratch_dir.mkdir(parents=True, exist_ok=True)
    hapi.db_begin(str(scratch_dir))

    out: dict[str, tuple[NDArray[np.float64], NDArray[np.float64]]] = {}
    for band, (nu_min, nu_max) in BAND_NU_RANGES.items():
        table = f"CH4_{band}"
        try:
            hapi.fetch_by_ids(table, CH4_ISO_GLOBAL_IDS, nu_min, nu_max)
        except Exception:
            hapi.fetch(table, 6, 1, nu_min, nu_max)
        nu, coef = hapi.absorptionCoefficient_Voigt(
            SourceTables=table,
            Environment={"T": TEMPERATURE_K, "p": PRESSURE_ATM},
            WavenumberStep=WAVENUMBER_STEP,
            HITRAN_units=True,  # σ in cm²/molecule
            Diluent={"air": 1.0},
        )
        out[band] = (np.asarray(nu, dtype=np.float64), np.asarray(coef, dtype=np.float64))
    return out


# ── LUT assembly ──


def build_lut_arrays(
    cross_sections: dict[str, tuple[NDArray[np.float64], NDArray[np.float64]]],
    srf: dict[str, NDArray[np.float64]],
) -> dict[str, NDArray[np.float64]]:
    """Assemble the (M, N) MBSP fractional-signal grids for S2A and S2B."""
    wl = srf["wavelength_nm"]
    nu_b11, sig_b11 = cross_sections["B11"]
    nu_b12, sig_b12 = cross_sections["B12"]

    n = DELTA_OMEGA_GRID.size
    m = AMF_GRID.size
    out: dict[str, NDArray[np.float64]] = {}
    for key in ("s2a", "s2b"):
        srf11 = srf_on_nu_grid(nu_b11, wl, srf[f"{key}_b11"])
        srf12 = srf_on_nu_grid(nu_b12, wl, srf[f"{key}_b12"])
        grid = np.empty((m, n), dtype=np.float64)
        for i, amf in enumerate(AMF_GRID):
            for j, d_omega in enumerate(DELTA_OMEGA_GRID):
                m11 = band_fractional_signal(
                    nu_b11, srf11, sig_b11, OMEGA_CH4_BACKGROUND_MOL_M2, float(d_omega), float(amf)
                )
                m12 = band_fractional_signal(
                    nu_b12, srf12, sig_b12, OMEGA_CH4_BACKGROUND_MOL_M2, float(d_omega), float(amf)
                )
                grid[i, j] = mbsp_fractional_signal(m11, m12)
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

    srf = load_srf_csv(SRF_CSV)
    cross_sections = compute_cross_sections(args.scratch)
    grids = build_lut_arrays(cross_sections, srf)

    provenance = {
        "hitran_fetch_date": datetime.now(UTC).date().isoformat(),
        "hitran_molecule": "CH4 (6)",
        "hitran_isotopologue_global_ids": CH4_ISO_GLOBAL_IDS,
        "srf_document": (
            "ESA Sentinel-2 Spectral Response Functions, "
            "COPE-GSEG-EOPG-TN-15-0007 issue 3.2 (2022), via scripts/data/s2_srf_b11_b12.csv"
        ),
        "temperature_k": TEMPERATURE_K,
        "pressure_atm": PRESSURE_ATM,
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
        version="1",
        provenance=json.dumps(provenance),
    )
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
