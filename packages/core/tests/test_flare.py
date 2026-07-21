"""NHI flare-hot detection (Phase 9) — translation math + calibration neutralisation."""

from __future__ import annotations

import numpy as np
import pytest

from openearth.methane.flare import SOLAR_IRRADIANCE, nhi_hot_mask
from openearth.methane.retrieval import _fit_c, mbsp


def _uniform(shape: tuple[int, int], r8a: float, r11: float, r12: float) -> dict[str, np.ndarray]:
    return {
        "B8A": np.full(shape, r8a),
        "B11": np.full(shape, r11),
        "B12": np.full(shape, r12),
    }


@pytest.mark.parametrize("spacecraft", ["Sentinel-2A", "Sentinel-2B"])
def test_nhi_translation_math_matches_hand_computed_radiances(spacecraft: str) -> None:
    e = SOLAR_IRRADIANCE[spacecraft]
    bands = _uniform((5, 5), r8a=0.4, r11=0.35, r12=0.30)  # ordinary bright desert

    # Hand-computed radiance proxies L_i = ρ_i·E_i (the shared cos/πd² factor cancels).
    def hot(r8a: float, r11: float, r12: float) -> bool:
        swir = r12 * e["B12"] > r11 * e["B11"]
        swnir = r11 * e["B11"] > r8a * e["B8A"]
        return (swir or swnir) and r12 >= 0.01

    assert not hot(0.4, 0.35, 0.30)  # normal surface is never hot
    assert not nhi_hot_mask(bands, spacecraft, dilate=False).any()

    # A SWIR-hot flare: ρ12/ρ11 above the E11/E12 threshold (2.881 S2A / 2.816 S2B).
    ratio = e["B11"] / e["B12"]
    bands["B11"][2, 2] = 0.1
    bands["B12"][2, 2] = 0.1 * (ratio + 0.5)  # comfortably over threshold
    assert hot(0.4, 0.1, 0.1 * (ratio + 0.5))
    mask = nhi_hot_mask(bands, spacecraft, dilate=False)
    assert mask[2, 2]
    assert int(mask.sum()) == 1


def test_nhi_reflectance_floor_suppresses_dark_pixels() -> None:
    # Ratio condition met but ρ12 below the 0.01 floor → dark-pixel noise, not hot.
    bands = _uniform((4, 4), r8a=0.001, r11=0.001, r12=0.005)
    assert not nhi_hot_mask(bands, "Sentinel-2A", dilate=False).any()


def test_nhi_nan_pixels_are_not_hot() -> None:
    bands = _uniform((4, 4), r8a=0.4, r11=0.1, r12=0.9)  # would be hot
    bands["B12"][0, 0] = np.nan
    assert not nhi_hot_mask(bands, "Sentinel-2A", dilate=False)[0, 0]


def test_nhi_dilation_grows_by_one_ring() -> None:
    bands = _uniform((7, 7), r8a=0.4, r11=0.1, r12=0.9)  # all hot
    # A single interior hot pixel dilates to a 3×3 block.
    cold = _uniform((7, 7), r8a=0.4, r11=0.35, r12=0.30)
    cold["B11"][3, 3] = 0.1
    cold["B12"][3, 3] = 0.9
    assert int(nhi_hot_mask(cold, "Sentinel-2A", dilate=False).sum()) == 1
    assert int(nhi_hot_mask(cold, "Sentinel-2A", dilate=True).sum()) == 9
    assert int(nhi_hot_mask(bands, "Sentinel-2A", dilate=False).sum()) == 49


def test_nhi_unknown_spacecraft_raises() -> None:
    with pytest.raises(ValueError, match="Unknown spacecraft"):
        nhi_hot_mask(_uniform((3, 3), 0.4, 0.35, 0.30), "Sentinel-9Z")


def test_exclude_drops_hot_cluster_from_initial_calibration() -> None:
    # A textured, plume-free background (so robust_sigma > 0, as on real chips)…
    rng = np.random.default_rng(0)
    shape = (32, 32)
    r11 = rng.uniform(0.18, 0.24, shape)
    r12 = r11 * (1.0 + rng.normal(0.0, 0.01, shape))  # ratio ≈ 1, no plume
    finite = np.isfinite(r11) & np.isfinite(r12)
    c_clean = _fit_c(r11, r12, finite)

    # …corrupted by a bright SWIR flare cluster (high ρ12/ρ11).
    r11c, r12c = r11.copy(), r12.copy()
    r11c[14:18, 14:18] = 0.1
    r12c[14:18, 14:18] = 0.9
    bands = {"B8A": np.full(shape, 0.4), "B11": r11c, "B12": r12c}
    hot = nhi_hot_mask(bands, "Sentinel-2A")

    # The flare drags the initial (pre-refit) calibration; excluding the NHI-hot
    # pixels removes it exactly (equals a manual fit over the non-hot pixels), so a
    # thermal hotspot never enters the calibration in the first place.
    legacy_initial = mbsp(r11c, r12c).c_initial
    excluded_initial = mbsp(r11c, r12c, robust_cut=True, exclude=hot).c_initial
    manual = _fit_c(r11c, r12c, finite & ~hot)
    assert excluded_initial == pytest.approx(manual)
    assert excluded_initial == pytest.approx(c_clean, abs=1e-3)
    assert abs(legacy_initial - c_clean) > abs(excluded_initial - c_clean)
