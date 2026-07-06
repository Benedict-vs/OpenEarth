"""Physical constants and calibrated coefficients for the methane suite.

Every value is cited inline. Literature values come from Varon et al. 2021
(*Atmos. Meas. Tech.* 14:2771, "High-frequency monitoring of anomalous methane
point sources with multispectral Sentinel-2 imagery", open access). Values that
are our own modeling choices (not from the literature) are flagged as such and
documented in ``docs/methane_methods.md``.
"""

from __future__ import annotations

# Avogadro constant (CODATA 2018, exact) — molecules per mole.
AVOGADRO_PER_MOL = 6.02214076e23

# Molar mass of methane (CH4): 12.011 + 4·1.008 g/mol ≈ 16.04 g/mol.
M_CH4_KG_PER_MOL = 0.01604

# Vertical column of dry air, mol/m²: P0 / (g · M_air)
#   = 101325 Pa / (9.80665 m/s² · 0.0289644 kg/mol) ≈ 3.567e5 mol/m².
OMEGA_AIR_MOL_M2 = 3.567e5

# Background CH4 column: a global mean mixing ratio of ~1875 ppb over the dry-air
# column above gives Ω0 ≈ 1875e-9 · 3.567e5 ≈ 0.65 mol/m² (Varon et al. 2021,
# Sect. 2). The retrieval measures enhancement ΔΩ on top of this background.
OMEGA_CH4_BACKGROUND_MOL_M2 = 0.65

# Effective wind speed for the S2 IME inversion, LES-calibrated against 10 m wind
# (Varon et al. 2021, Sect. 3): U_eff = 0.33 · U10 + 0.45  [m/s].
UEFF_ALPHA = 0.33
UEFF_BETA_MS = 0.45

# ── Modeling choices, NOT literature values (see docs/methane_methods.md) ──
# Reanalysis 10 m wind 1σ error floor. Varon uses GEOS-FP-vs-mesonet residuals
# we cannot reproduce here; this is an honest, documented floor.
SIGMA_U10_FLOOR_MS = 1.5
# Multiplicative IME-model (mass-balance) error folded into the Monte Carlo.
IME_MODEL_SIGMA_FRAC = 0.15
