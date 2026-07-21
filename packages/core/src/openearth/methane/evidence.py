"""False-positive evidence checks for a methane detection — pure NumPy, mypy strict.

These are Kayrros-style physical sanity checks that a masked component behaves like
a real methane plume rather than a surface or reference artefact. Each returns a
statistic or a flag; none gates the pipeline this phase (flag-only).

  * ``b12_dimming_ok`` — a real plume *absorbs* in B12, so the target's single-pass
    ΔR = (c·R12 − R11)/R11 is negative inside the plume; an in-mask mean ΔR ≥ 0
    means the "plume" is driven by something else (Ehret et al. 2022 dimming sign).
  * ``surface_correlation`` — S2 methane plumes are essentially invisible in the
    visible bands; a mask that correlates with B4/B3/B2 tracks a surface feature.
  * ``chip_flags`` — per-chip validity: too few finite pixels, or a bright-blue
    cloud/haze proxy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

if TYPE_CHECKING:
    from collections.abc import Mapping

# ── Declared modeling constants (see docs/methane_methods.md §7) ──
# |point-biserial r| above this → the mask tracks a visible-band surface feature.
SURFACE_CORRELATION_CUT = 0.5
# Below this finite-pixel fraction a chip is too sparse to trust.
SPARSE_FINITE_FRACTION = 0.7
# Mean B2 (blue) TOA reflectance above this is a cloud/haze proxy.
CLOUDY_B2_MEAN = 0.25

_RING_PX = 3
_CONNECTIVITY_8 = np.ones((3, 3), dtype=bool)
_BLIND_BANDS = ("B4", "B3", "B2")


def b12_dimming_ok(delta_r_target: NDArray[np.float64], mask: NDArray[np.bool_]) -> bool:
    """True when the plume shows B12 dimming (in-mask mean target ΔR < 0).

    Returns True when there is nothing to assess (empty/all-NaN mask), so the
    ``not_b12_dimming`` flag never fires spuriously on an absent plume.
    """
    in_mask = delta_r_target[mask & np.isfinite(delta_r_target)]
    if in_mask.size == 0:
        return True
    return bool(np.mean(in_mask) < 0.0)


def _point_biserial(indicator: NDArray[np.float64], values: NDArray[np.float64]) -> float:
    """|Pearson r| between a 0/1 indicator and a continuous field (0 if degenerate)."""
    if indicator.size < 3:
        return 0.0
    if float(indicator.std()) == 0.0 or float(values.std()) == 0.0:
        return 0.0
    return abs(float(np.corrcoef(indicator, values)[0, 1]))


def surface_correlation(
    mask: NDArray[np.bool_],
    blind_bands: Mapping[str, NDArray[Any]],
    *,
    ring_px: int = _RING_PX,
) -> float:
    """Max |point-biserial r| between the mask indicator and each of B4/B3/B2.

    Evaluated over ``mask ∪ ring`` (the mask plus a *ring_px*-wide surrounding
    ring), with the indicator 1 on the mask and 0 on the ring. Returns 0.0 when
    the domain is degenerate.
    """
    if not mask.any():
        return 0.0
    ring = ndimage.binary_dilation(mask, structure=_CONNECTIVITY_8, iterations=ring_px) & ~mask
    if not ring.any():
        return 0.0
    domain = mask | ring
    best = 0.0
    for name in _BLIND_BANDS:
        band = np.asarray(blind_bands[name], dtype=np.float64)
        sel = domain & np.isfinite(band)
        if int(sel.sum()) < 3:
            continue
        indicator = mask[sel].astype(np.float64)
        best = max(best, _point_biserial(indicator, band[sel]))
    return best


def chip_flags(bands: Mapping[str, NDArray[Any]]) -> list[str]:
    """Per-chip validity flags: ``sparse_chip`` and/or ``cloudy_chip`` (or none)."""
    flags: list[str] = []
    b12 = np.asarray(bands["B12"], dtype=np.float64)
    if b12.size and float(np.isfinite(b12).mean()) < SPARSE_FINITE_FRACTION:
        flags.append("sparse_chip")
    b2 = np.asarray(bands["B2"], dtype=np.float64)
    finite_b2 = b2[np.isfinite(b2)]
    if finite_b2.size and float(finite_b2.mean()) > CLOUDY_B2_MEAN:
        flags.append("cloudy_chip")
    return flags
