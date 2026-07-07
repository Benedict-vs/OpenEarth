"""Integrated Mass Enhancement (IME) quantification with joint Monte-Carlo
uncertainty.

Pure NumPy — mypy strict, no exemptions. The IME mass-balance inversion follows
Varon et al. 2021: ``Q = U_eff / L · IME``, with ``U_eff`` the LES-calibrated
effective wind and ``L`` the plume length. Uncertainty is propagated by a
seeded Monte Carlo that jointly perturbs the masking threshold, the wind, the
retrieval noise (off-plume bootstrap), and the mass-balance model error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from openearth.methane.constants import (
    IME_MODEL_SIGMA_FRAC,
    M_CH4_KG_PER_MOL,
    UEFF_ALPHA,
    UEFF_BETA_MS,
)
from openearth.methane.plume import PlumeMask, detect_plume, pixel_area_m2, robust_sigma

if TYPE_CHECKING:
    from openearth.ee.pixels import GridSpec
    from openearth.methane.wind import WindSample


def ime_kg(delta_omega: NDArray[np.float64], mask: NDArray[np.bool_], grid: GridSpec) -> float:
    """Integrated methane mass over *mask* (kg): Σ ΔΩ · A_pix · M_CH4.

    NaN pixels inside the mask contribute 0 (count them separately as a QC flag).
    """
    a_pix = pixel_area_m2(grid)
    masked = np.where(mask, delta_omega, 0.0)
    total_mol_per_m2 = float(np.nansum(masked))
    return total_mol_per_m2 * a_pix * M_CH4_KG_PER_MOL


def plume_length_m(mask: NDArray[np.bool_], grid: GridSpec) -> float:
    """Characteristic plume length L = √(n_px · A_pix) (m)."""
    n_px = int(mask.sum())
    return float(np.sqrt(n_px * pixel_area_m2(grid)))


def u_eff_ms(u10: float) -> float:
    """LES-calibrated effective wind speed U_eff = α·U10 + β (m/s)."""
    return UEFF_ALPHA * u10 + UEFF_BETA_MS


@dataclass(frozen=True)
class McParams:
    """Monte-Carlo settings (all seeded for bit-for-bit reproducibility)."""

    n: int = 500
    seed: int = 0
    k_grid: tuple[float, ...] = (1.5, 1.75, 2.0, 2.25, 2.5)


# Module-level default so it isn't constructed in a function signature (B008).
_DEFAULT_MC = McParams()


@dataclass(frozen=True)
class EmissionEstimate:
    """Quantified emission rate with the full uncertainty budget."""

    q_kg_h: float  # MC median
    q_sigma_kg_h: float  # MC std
    percentiles: dict[str, float]  # p05, p25, p50, p75, p95
    histogram: dict[str, list[float]]  # {"edges": 25, "counts": 24}
    ime_kg: float
    l_m: float
    u_eff_ms: float
    u10_ms: float
    sigma_u10_ms: float
    wind_from_deg: float
    n_mc: int
    # Robust σ of the off-plume ΔΩ (mol/m²) population — the retrieval-noise scale that
    # feeds the MC bootstrap. Distinct from the mask threshold σ (which is in ΔR units when
    # masking in ΔR space); see ``PlumeMask.sigma``. NaN when there is no plume.
    sigma_noise_delta_omega: float = float("nan")


def _nan_estimate(
    u10: float, sigma_u10: float, wind_from_deg: float, n_mc: int
) -> EmissionEstimate:
    """The estimate returned when there is no plume to quantify."""
    nan = float("nan")
    return EmissionEstimate(
        q_kg_h=nan,
        q_sigma_kg_h=nan,
        percentiles={p: nan for p in ("p05", "p25", "p50", "p75", "p95")},
        histogram={"edges": [], "counts": []},
        ime_kg=0.0,
        l_m=0.0,
        u_eff_ms=u_eff_ms(u10),
        u10_ms=u10,
        sigma_u10_ms=sigma_u10,
        wind_from_deg=wind_from_deg,
        n_mc=n_mc,
        sigma_noise_delta_omega=nan,
    )


def quantify(
    delta_omega: NDArray[np.float64],
    grid: GridSpec,
    wind: WindSample,
    sigma_u10: float,
    *,
    mask_field: NDArray[np.float64] | None = None,
    k_sigma: float = 2.0,
    min_area_px: int = 5,
    source_rc: tuple[int, int] | None = None,
    mc: McParams = _DEFAULT_MC,
) -> tuple[EmissionEstimate, PlumeMask]:
    """Quantify the emission rate and its uncertainty from a ΔΩ field.

    The plume footprint is thresholded on *mask_field* (defaults to *delta_omega*);
    passing the LUT-independent ``−ΔR`` field there makes the mask invariant to the
    inversion calibration while the mass (IME) and retrieval-noise bootstrap stay in
    ΔΩ. The display mask (and the returned ``PlumeMask``) is the ``k_sigma`` one; the
    Monte Carlo additionally jitters the threshold over ``mc.k_grid``.
    """
    u10 = wind.speed_ms
    field = delta_omega if mask_field is None else mask_field
    display = detect_plume(
        field, grid, k_sigma=k_sigma, min_area_px=min_area_px, source_rc=source_rc
    )
    if display.n_pixels == 0:
        return _nan_estimate(u10, sigma_u10, wind.wind_from_deg, mc.n), display

    a_pix = pixel_area_m2(grid)
    # Off-plume ΔΩ population for the retrieval-noise bootstrap: finite pixels
    # outside the display mask (the plume must not inflate its own noise).
    off_plume = delta_omega[(~display.mask) & np.isfinite(delta_omega)]
    sigma_noise = robust_sigma(off_plume) if off_plume.size else float("nan")

    # Precompute mask/IME/length once per threshold k (5 labelings, not 500). The mask
    # is thresholded on *field*; the mass it integrates stays in ΔΩ.
    ime_k = np.empty(len(mc.k_grid))
    length_k = np.empty(len(mc.k_grid))
    npix_k = np.empty(len(mc.k_grid), dtype=np.intp)
    for i, k in enumerate(mc.k_grid):
        pm = detect_plume(field, grid, k_sigma=k, min_area_px=min_area_px, source_rc=source_rc)
        ime_k[i] = ime_kg(delta_omega, pm.mask, grid)
        length_k[i] = plume_length_m(pm.mask, grid)
        npix_k[i] = pm.n_pixels

    rng = np.random.default_rng(mc.seed)
    k_choice = rng.integers(0, len(mc.k_grid), mc.n)
    u10_draw = np.maximum(0.1, rng.normal(u10, sigma_u10, mc.n))  # truncated at ≥ 0.1
    model_factor = rng.normal(1.0, IME_MODEL_SIGMA_FRAC, mc.n)

    # Retrieval noise: for each draw, the signed sum of n_px bootstrapped
    # off-plume ΔΩ values, in kg. Grouped by k so each is one vectorized draw.
    noise_ime = np.zeros(mc.n)
    if off_plume.size > 0:
        for i in range(len(mc.k_grid)):
            idx = np.flatnonzero(k_choice == i)
            n_px = int(npix_k[i])
            if idx.size == 0 or n_px == 0:
                continue
            samples = rng.choice(off_plume, size=(idx.size, n_px), replace=True)
            noise_ime[idx] = samples.sum(axis=1) * a_pix * M_CH4_KG_PER_MOL

    ime_draw = ime_k[k_choice] + noise_ime
    length_draw = length_k[k_choice]
    u_eff_draw = UEFF_ALPHA * u10_draw + UEFF_BETA_MS
    with np.errstate(divide="ignore", invalid="ignore"):
        q_draw = u_eff_draw / length_draw * ime_draw * 3600.0 * model_factor
    q_draw = q_draw[np.isfinite(q_draw)]

    percentiles = {
        name: float(np.percentile(q_draw, p))
        for name, p in (("p05", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))
    }
    edges = np.linspace(float(q_draw.min()), float(q_draw.max()), 25)
    counts, _ = np.histogram(q_draw, bins=edges)

    estimate = EmissionEstimate(
        q_kg_h=float(np.median(q_draw)),
        q_sigma_kg_h=float(np.std(q_draw)),
        percentiles=percentiles,
        histogram={"edges": edges.tolist(), "counts": counts.astype(float).tolist()},
        ime_kg=ime_kg(delta_omega, display.mask, grid),
        l_m=plume_length_m(display.mask, grid),
        u_eff_ms=u_eff_ms(u10),
        u10_ms=u10,
        sigma_u10_ms=sigma_u10,
        wind_from_deg=wind.wind_from_deg,
        n_mc=mc.n,
        sigma_noise_delta_omega=sigma_noise,
    )
    return estimate, display
