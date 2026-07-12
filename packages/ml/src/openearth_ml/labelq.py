"""Label-quality gate for the training positives (fix 7 / Tier 2 F3).

A CH4Net positive whose own MBMP ΔR integrates to a *net-negative* ΔΩ over its
label footprint is internally contradictory — the "plume" has negative column
enhancement in our rebuilt chip (recovered-date error, reference contamination,
or annotation noise; the review measured 65/395 ≈ 16 %). These are excluded from
training and from the primary eval truth. Aggregate-only — no per-tile export.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from openearth.methane.constants import M_CH4_KG_PER_MOL, UEFF_ALPHA, UEFF_BETA_MS
from openearth.methane.conversion import invert_fractional_signal, load_lut
from openearth_ml.data import ChipRef

# Nominal solar-geometry AMF for the offline label-Q inversion (Sentinel-2A). The
# gate is sign-based (integral ≤ 0) and invert_fractional_signal is monotonic
# through ΔΩ = 0, so the exact AMF only scales the magnitude — see Tier 2 F3.
_NOMINAL_AMF = 2.3
_SPACECRAFT = "Sentinel-2A"
# Nominal geometry for the order-of-magnitude label-Q estimate (Tier 2 F3): the
# median Tier 1 wind and a 20 m pixel. Only used to place labels against the noise
# floor — never a rate we report per chip.
_NOMINAL_U10_MS = 3.98
_PIXEL_AREA_M2 = 400.0


def label_integral_delta_omega(ref: ChipRef, amf: float = _NOMINAL_AMF) -> float:
    """Integrate reporting-LUT ΔΩ (from the chip's own MBMP ΔR, channel 0) over the
    CH4Net label footprint. ≤ 0 means a net-negative, internally contradictory label."""
    z = np.load(ref.path)
    delta_r = z["channels"][..., 0].astype(np.float64)  # MBMP ΔR
    mask = z["mask"].astype(bool)
    if not mask.any():
        return 0.0
    d_omega = invert_fractional_signal(delta_r, load_lut(), _SPACECRAFT, amf)
    return float(np.nansum(np.where(mask, d_omega, 0.0)))


def label_q_kg_h(ref: ChipRef, u10: float = _NOMINAL_U10_MS) -> float:
    """Order-of-magnitude label emission rate (kg/h) for placing labels against the
    noise floor: Q = U_eff/L · IME · 3600 over the label footprint. Not a per-chip rate."""
    n_px = int(np.load(ref.path)["mask"].astype(bool).sum())
    if n_px == 0:
        return 0.0
    ime = label_integral_delta_omega(ref) * _PIXEL_AREA_M2 * M_CH4_KG_PER_MOL
    length = (n_px * _PIXEL_AREA_M2) ** 0.5
    return (UEFF_ALPHA * u10 + UEFF_BETA_MS) / length * ime * 3600.0


@dataclass(frozen=True)
class LabelQuality:
    """The label gate's partition (aggregate counts only)."""

    kept: list[ChipRef]  # positives with integral > 0, plus every negative
    excluded: list[ChipRef]  # positives with ΔΩ integral ≤ 0
    n_positive: int
    n_excluded: int


def quality_filter(refs: list[ChipRef]) -> LabelQuality:
    """Partition *refs*: keep positives whose ΔΩ integral > 0 (plus all negatives);
    exclude net-negative positives from training and the primary eval truth."""
    kept: list[ChipRef] = []
    excluded: list[ChipRef] = []
    n_positive = 0
    for ref in refs:
        if not ref.positive:
            kept.append(ref)
            continue
        n_positive += 1
        if label_integral_delta_omega(ref) > 0.0:
            kept.append(ref)
        else:
            excluded.append(ref)
    return LabelQuality(kept, excluded, n_positive, len(excluded))
