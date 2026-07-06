"""Load the committed CH4 absorption LUT and convert MBSP/MBMP fractional
signal ΔR to a methane column enhancement ΔΩ (and ΔXCH4).

The LUT (``methane/data/ch4_lut_v3.npz``) is generated offline by
``scripts/generate_ch4_lut.py`` (HITRAN + Sentinel-2 SRFs) and shipped inside
the package; nothing here touches Earth Engine or HAPI. This module is pure
NumPy and passes mypy strict with no exemptions.

The forward model per band is a Beer–Lambert, SRF-weighted band transmittance
ratio relative to the background column Ω0 (see the generator and
``docs/methane_methods.md``). ``m`` stored in the LUT is already the MBSP
fractional signal ``(1 + m_B12) / (1 + m_B11) − 1`` per (AMF, ΔΩ), computed
separately for Sentinel-2A and Sentinel-2B.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from openearth.methane.constants import OMEGA_AIR_MOL_M2

_METHANE_PACKAGE = "openearth.methane"
_LUT_FILENAME = "ch4_lut_v3.npz"

# Map the compact npz array keys to full SPACECRAFT_NAME values used everywhere
# else (S2Scene.spacecraft, retrieval, the API).
_SPACECRAFT_KEYS = {"Sentinel-2A": "m_s2a", "Sentinel-2B": "m_s2b"}


@dataclass(frozen=True)
class CH4Lut:
    """CH4 MBSP fractional-signal lookup table over (AMF, ΔΩ)."""

    delta_omega: NDArray[np.float64]  # (N,) mol/m²
    amf: NDArray[np.float64]  # (M,) air mass factor grid
    m: dict[str, NDArray[np.float64]]  # {"Sentinel-2A": (M,N), "Sentinel-2B": (M,N)}
    version: str
    provenance: str


def _packaged_lut_path() -> Path:
    return Path(str(files(_METHANE_PACKAGE).joinpath("data", _LUT_FILENAME)))


@lru_cache(maxsize=4)
def _load_lut_cached(path_str: str) -> CH4Lut:
    with np.load(path_str, allow_pickle=False) as npz:
        delta_omega = np.asarray(npz["delta_omega"], dtype=np.float64)
        amf = np.asarray(npz["amf"], dtype=np.float64)
        m = {name: np.asarray(npz[key], dtype=np.float64) for name, key in _SPACECRAFT_KEYS.items()}
        version = str(npz["version"])
        provenance = str(npz["provenance"])
    # Validate the provenance is JSON so a corrupt artifact fails loudly at load.
    json.loads(provenance)
    return CH4Lut(delta_omega=delta_omega, amf=amf, m=m, version=version, provenance=provenance)


def load_lut(path: Path | None = None) -> CH4Lut:
    """Load the CH4 LUT (cached). *path* defaults to the packaged ``ch4_lut_v3.npz``."""
    resolved = path if path is not None else _packaged_lut_path()
    return _load_lut_cached(str(resolved))


def forward_signal(
    lut: CH4Lut, spacecraft: str, amf: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """The (ΔΩ, m) forward curve for *spacecraft* at *amf*.

    Linear interpolation along the AMF axis; *amf* is clamped into the grid
    range (the AMF only shifts the curve gently, so extrapolation is neither
    needed nor wise).
    """
    if spacecraft not in lut.m:
        raise ValueError(f"Unknown spacecraft {spacecraft!r}; expected one of {list(lut.m)}.")
    m2d = lut.m[spacecraft]
    a = float(np.clip(amf, float(lut.amf[0]), float(lut.amf[-1])))
    # Column-wise linear interp along the (short) AMF axis; N is small.
    row = np.array(
        [np.interp(a, lut.amf, m2d[:, n]) for n in range(m2d.shape[1])], dtype=np.float64
    )
    return lut.delta_omega, row


def invert_fractional_signal(
    delta_r: NDArray[np.float64], lut: CH4Lut, spacecraft: str, amf: float
) -> NDArray[np.float64]:
    """Invert the forward curve: ΔR fractional signal → ΔΩ (mol/m²).

    ``m`` is monotonically decreasing in ΔΩ, so the curve is reversed to feed
    ``np.interp`` (which needs increasing x). NaN passes through; ΔR values
    outside the tabulated ``m`` range clip to the grid ends.
    """
    delta_omega, m = forward_signal(lut, spacecraft, amf)
    # Reverse so xp (m) is strictly increasing for np.interp.
    m_rev = m[::-1]
    do_rev = delta_omega[::-1]
    return np.asarray(np.interp(delta_r, m_rev, do_rev), dtype=np.float64)


def delta_omega_to_xch4_ppb(
    delta_omega: NDArray[np.float64] | float,
) -> NDArray[np.float64] | float:
    """Convert a CH4 column enhancement ΔΩ (mol/m²) to a ΔXCH4 in ppb.

    ΔXCH4 = ΔΩ / Ω_air · 1e9, with Ω_air the dry-air column. Sanity: a doubled
    background (0.65 mol/m²) maps to ≈ 1822 ppb.
    """
    out: NDArray[np.float64] = np.asarray(delta_omega, dtype=np.float64) / OMEGA_AIR_MOL_M2 * 1e9
    if np.isscalar(delta_omega):
        return float(out)
    return out
