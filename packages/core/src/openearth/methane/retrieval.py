"""Sentinel-2 TOA chip fetch + calibrated MBSP/MBMP fractional-signal retrieval.

The only Earth Engine surface is :func:`_build_scene_image` /
:func:`fetch_chip`, which reuses ``ee.pixels`` (``grid_for`` / ``fetch_pixels``)
unchanged. The band math (:func:`mbsp`, :func:`mbmp`) is pure NumPy and
unit-tested offline.

MBSP (Varon et al. 2021): a single scene's fractional signal
``ΔR = (c·R12 − R11) / R11`` with ``c`` the zero-intercept least-squares slope of
R11 on R12, refit once excluding the plume so it cannot bias its own
calibration. MBMP subtracts a reference scene's ΔR to cancel static surface
structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import ee
import numpy as np
from numpy.typing import NDArray

from openearth.catalog.builtin.s2 import S2_COLLECTION_ID
from openearth.ee.pixels import GridSpec, fetch_pixels, grid_for

if TYPE_CHECKING:
    from openearth.geometry import BBox
    from openearth.methane.scenes import S2Scene

# B11/B12 drive the retrieval; B4/B3/B2 are the UI's RGB context (5 ≤ MAX_BANDS).
# S2_HARMONIZED band ids are unpadded (B4, not B04) — verified against the live
# collection's Available bands list.
CHIP_BANDS = ("B11", "B12", "B4", "B3", "B2")

# Explicit fill for EE-masked pixels — set before the fetch, mapped to NaN after
# (never treat a legitimate DN 0 as fill).
_FILL = -9999.0

# A chip larger than this in either dimension is almost certainly a fat-fingered
# bbox; refuse it rather than fan out into a windowed mega-fetch.
_MAX_CHIP_PX = 1024


@dataclass(frozen=True)
class RetrievalChip:
    """One scene's TOA reflectance bands on a shared EPSG:4326 grid."""

    scene: S2Scene
    grid: GridSpec
    bands: dict[str, NDArray[np.float32]]  # (H,W) reflectance; NaN = masked


@dataclass(frozen=True)
class MbspResult:
    """MBSP fractional signal plus its calibration provenance."""

    delta_r: NDArray[np.float64]  # (H,W), NaN-safe
    c: float  # final calibration constant
    c_initial: float  # before the plume-excluding refit
    n_excluded: int  # pixels dropped by the refit


def _build_scene_image(scene_id: str) -> ee.Image:
    """The single L1C scene as an image with EE-masked pixels set to ``_FILL``."""
    return ee.Image(f"{S2_COLLECTION_ID}/{scene_id}").select(list(CHIP_BANDS)).unmask(_FILL)


def _fill_to_reflectance(
    cube: NDArray[np.float32], bands: tuple[str, ...]
) -> dict[str, NDArray[np.float32]]:
    """Split a ``(H,W,B)`` DN cube into per-band reflectance, ``_FILL`` → NaN."""
    out: dict[str, NDArray[np.float32]] = {}
    for i, band in enumerate(bands):
        dn = cube[:, :, i]
        refl = (dn / 1e4).astype(np.float32)
        refl[dn == _FILL] = np.nan
        out[band] = refl
    return out


def fetch_chip(scene: S2Scene, bbox: BBox, *, scale_m: int = 20) -> RetrievalChip:
    """Fetch *scene*'s TOA reflectance chip over *bbox* on a shared grid.

    Refuses grids larger than 1024×1024 (a bbox that big would fan out into a
    windowed mega-fetch). Masked pixels arrive as ``_FILL`` and become NaN;
    DN are scaled to reflectance by 1e4 (S2_HARMONIZED offsets are already
    harmonized, so this holds across years).
    """
    grid = grid_for(bbox, scale_m)
    if grid.width > _MAX_CHIP_PX or grid.height > _MAX_CHIP_PX:
        raise ValueError(
            f"Refusing a {grid.width}×{grid.height} chip (limit "
            f"{_MAX_CHIP_PX}²); use a smaller bbox or coarser scale."
        )
    image = _build_scene_image(scene.scene_id)
    cube = fetch_pixels(image, grid, list(CHIP_BANDS))
    return RetrievalChip(scene=scene, grid=grid, bands=_fill_to_reflectance(cube, CHIP_BANDS))


def _fit_c(r11: NDArray[np.float64], r12: NDArray[np.float64], mask: NDArray[np.bool_]) -> float:
    """Zero-intercept least-squares slope of R11 on R12 over *mask*."""
    num = float(np.nansum(np.where(mask, r11 * r12, 0.0)))
    den = float(np.nansum(np.where(mask, r12 * r12, 0.0)))
    if den == 0.0:
        return float("nan")
    return num / den


def _delta_r(r11: NDArray[np.float64], r12: NDArray[np.float64], c: float) -> NDArray[np.float64]:
    """ΔR = (c·R12 − R11)/R11, NaN where R11 is non-finite or zero."""
    with np.errstate(divide="ignore", invalid="ignore"):
        dr = (c * r12 - r11) / r11
    dr = np.asarray(dr, dtype=np.float64)
    dr[~np.isfinite(r11) | (r11 == 0.0)] = np.nan
    return dr


def mbsp(r11: NDArray[np.float64], r12: NDArray[np.float64]) -> MbspResult:
    """Calibrated MBSP fractional signal with a single plume-excluding refit."""
    r11 = np.asarray(r11, dtype=np.float64)
    r12 = np.asarray(r12, dtype=np.float64)
    valid = np.isfinite(r11) & np.isfinite(r12)

    c_initial = _fit_c(r11, r12, valid)
    dr0 = _delta_r(r11, r12, c_initial)
    sigma = float(np.nanstd(dr0))

    # Drop |ΔR| > 1σ so a real plume can't drag the calibration toward itself.
    keep = valid & np.isfinite(dr0) & (np.abs(dr0) <= sigma)
    # A flat/degenerate field (σ ≈ 0) would leave nothing to refit on; keep the
    # initial calibration rather than discarding every pixel.
    if not np.isfinite(sigma) or sigma == 0.0 or np.count_nonzero(keep) < 3:
        return MbspResult(delta_r=dr0, c=c_initial, c_initial=c_initial, n_excluded=0)

    n_excluded = int(np.count_nonzero(valid & np.isfinite(dr0) & (np.abs(dr0) > sigma)))
    c = _fit_c(r11, r12, keep)
    return MbspResult(
        delta_r=_delta_r(r11, r12, c), c=c, c_initial=c_initial, n_excluded=n_excluded
    )


def mbmp(target: MbspResult, reference: MbspResult) -> NDArray[np.float64]:
    """MBMP fractional signal: element-wise ΔR_target − ΔR_reference.

    Grids are identical by construction (same bbox + scale ⇒ same GridSpec;
    EE resamples each scene onto it), so this is a plain array subtraction.
    """
    return np.asarray(target.delta_r - reference.delta_r, dtype=np.float64)
