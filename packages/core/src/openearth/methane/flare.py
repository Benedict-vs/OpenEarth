"""NHI hot-pixel (flare) detection, translated to TOA-reflectance chips.

A lit gas flare combusts most of its methane but emits a strong shortwave-infrared
thermal signal that corrupts the B11/B12 retrieval; a lit→unlit transition between
the target and reference scenes can even mimic a plume at the stack. The Normalized
Hotspot Index (Marchese et al. 2019, *Remote Sens.* 11:2213) flags such pixels.

NHI is defined on TOA **radiance** L:

    NHI_SWIR  = (L2.2 − L1.6)/(L2.2 + L1.6)   (B12, B11)   hot ⇔ > 0
    NHI_SWNIR = (L1.6 − L0.8)/(L1.6 + L0.8)   (B11, B8A)   hot ⇔ > 0

with a hot pixel where either index is positive. Our chips are TOA **reflectance**
ρ, and L_i = ρ_i·E_i·cos(SZA)/(π d²) shares the factor cos(SZA)/(π d²) across all
bands, so the *sign* conditions translate exactly to (E = solar irradiance):

    NHI_SWIR  > 0  ⇔  ρ12·E12 > ρ11·E11   (ρ12/ρ11 > E11/E12 ≈ 2.881 S2A / 2.816 S2B)
    NHI_SWNIR > 0  ⇔  ρ11·E11 > ρ8A·E8A

The reference implementation's *absolute* SWIR radiance floor does not translate
scale-free; we replace it with a declared reflectance floor (``_HOT_REFLECTANCE_FLOOR``)
that suppresses dark-pixel ratio noise, and dilate the hot set by one pixel so the
thermal bleed into neighbours is excluded too. Hotness additionally requires the
ρ8A/ρ11 entering the sign conditions to be non-negative — reflectance is physically
non-negative, and negative numerical artifacts (dark pixels under L1C DN offsets,
simulation noise) would otherwise satisfy the SWNIR condition trivially. This is OUR
documented adaptation of NHI to reflectance chips — pure NumPy, mypy strict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

if TYPE_CHECKING:
    from collections.abc import Mapping

# Sentinel-2 in-band solar irradiances E_i [W m⁻² µm⁻¹], read from the live
# COPERNICUS/S2_HARMONIZED L1C metadata (SOLAR_IRRADIANCE_Bx), per spacecraft.
# Only the SWNIR/SWIR bands the NHI sign conditions use are needed.
SOLAR_IRRADIANCE: dict[str, dict[str, float]] = {
    "Sentinel-2A": {"B8A": 955.19, "B11": 245.59, "B12": 85.25},
    "Sentinel-2B": {"B8A": 953.93, "B11": 247.08, "B12": 87.75},
}

# ── Declared modeling constants (NOT literature values) ──
# Reflectance floor replacing NHI's absolute radiance floor: below this ρ12 the
# ratio conditions are dark-pixel noise, so no pixel is called hot there.
_HOT_REFLECTANCE_FLOOR = 0.01
# 8-connectivity 1-px dilation of the hot set (thermal bleed into neighbours).
_CONNECTIVITY_8 = np.ones((3, 3), dtype=bool)


def _nhi_raw_hot(bands: Mapping[str, NDArray[Any]], spacecraft: str) -> NDArray[np.bool_]:
    """The un-dilated NHI hot set: SWIR OR SWNIR positive, above the ρ12 floor.

    NaN pixels compare False on every condition, so they are never hot.
    """
    if spacecraft not in SOLAR_IRRADIANCE:
        raise ValueError(
            f"Unknown spacecraft {spacecraft!r}; expected one of {list(SOLAR_IRRADIANCE)}."
        )
    e = SOLAR_IRRADIANCE[spacecraft]
    r8a = np.asarray(bands["B8A"], dtype=np.float64)
    r11 = np.asarray(bands["B11"], dtype=np.float64)
    r12 = np.asarray(bands["B12"], dtype=np.float64)
    with np.errstate(invalid="ignore"):
        swir_hot = r12 * e["B12"] > r11 * e["B11"]
        swnir_hot = r11 * e["B11"] > r8a * e["B8A"]
        above_floor = r12 >= _HOT_REFLECTANCE_FLOOR
        # Physical validity, not a tunable threshold: reflectance is non-negative,
        # so a negative ρ (dark-pixel numerical artifact — L1C DN offsets, sim
        # noise) is invalid data and must never satisfy a hotness condition. A
        # negative ρ8A would otherwise make the SWNIR condition trivially true.
        valid = (r8a >= 0.0) & (r11 >= 0.0)
    return np.asarray((swir_hot | swnir_hot) & above_floor & valid, dtype=bool)


def nhi_hot_mask(
    bands: Mapping[str, NDArray[Any]], spacecraft: str, *, dilate: bool = True
) -> NDArray[np.bool_]:
    """Boolean flare-hot mask for a chip (needs B8A, B11, B12).

    With ``dilate=True`` (the exclusion footprint) the hot set is grown by one
    8-connected pixel; ``dilate=False`` returns the raw hot pixels (the honest
    count for the ``flare_lit_*`` flag).
    """
    hot = _nhi_raw_hot(bands, spacecraft)
    if dilate and hot.any():
        hot = ndimage.binary_dilation(hot, structure=_CONNECTIVITY_8, iterations=1)
    return np.asarray(hot, dtype=bool)
