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
from openearth.methane.plume import robust_sigma

if TYPE_CHECKING:
    from openearth.geometry import BBox
    from openearth.methane.scenes import S2Scene

# B11/B12 drive the retrieval; B4/B3/B2 are the UI's RGB context; B8A feeds the
# NHI SWNIR flare condition (Phase 9). 6 = MAX_BANDS (no cap change). S2_HARMONIZED
# band ids are unpadded (B4, not B04) — verified against the live collection's
# Available bands list.
CHIP_BANDS = ("B11", "B12", "B4", "B3", "B2", "B8A")

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


def check_chip_bbox(bbox: BBox, *, scale_m: int = 20) -> None:
    """Raise ``ValueError`` if *bbox* would exceed the chip-size limit.

    Pure grid math (no EE round-trip), so callers can validate a bbox at
    request time instead of failing minutes into a job.
    """
    grid = grid_for(bbox, scale_m)
    if grid.width > _MAX_CHIP_PX or grid.height > _MAX_CHIP_PX:
        raise ValueError(
            f"Refusing a {grid.width}×{grid.height} chip (limit "
            f"{_MAX_CHIP_PX}²); use a smaller bbox or coarser scale."
        )


def fetch_chip(scene: S2Scene, bbox: BBox, *, scale_m: int = 20) -> RetrievalChip:
    """Fetch *scene*'s TOA reflectance chip over *bbox* on a shared grid.

    Refuses grids larger than 1024×1024 (a bbox that big would fan out into a
    windowed mega-fetch). Masked pixels arrive as ``_FILL`` and become NaN;
    DN are scaled to reflectance by 1e4 (S2_HARMONIZED offsets are already
    harmonized, so this holds across years).
    """
    check_chip_bbox(bbox, scale_m=scale_m)
    grid = grid_for(bbox, scale_m)
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


def mbsp(
    r11: NDArray[np.float64],
    r12: NDArray[np.float64],
    *,
    robust_cut: bool = False,
    exclude: NDArray[np.bool_] | None = None,
) -> MbspResult:
    """Calibrated MBSP fractional signal with a single plume-excluding refit.

    The defaults reproduce the legacy behaviour **bit-for-bit** — the ML seam
    (``channels.build_channels``) relies on this and a golden parity test enforces
    it. Opt-in robustness (Phase 9):

    * ``robust_cut`` swaps the refit's exclusion σ from ``np.nanstd`` (which a
      strong plume inflates) to the MAD-based :func:`plume.robust_sigma`.
    * ``exclude`` (a bool mask, e.g. NHI flare-hot pixels) drops pixels from
      **both** the initial and the refit calibration (and from the σ estimate),
      so a thermal hotspot cannot bias ``c``.
    """
    r11 = np.asarray(r11, dtype=np.float64)
    r12 = np.asarray(r12, dtype=np.float64)
    valid = np.isfinite(r11) & np.isfinite(r12)
    if exclude is not None:
        valid = valid & ~np.asarray(exclude, dtype=bool)

    c_initial = _fit_c(r11, r12, valid)
    dr0 = _delta_r(r11, r12, c_initial)
    # dr0 is already NaN outside {r11,r12 finite, r11≠0}, so with the default
    # exclude=None this masking is a no-op and the legacy path is reproduced
    # exactly; with an exclude it also removes the hot pixels from the σ estimate.
    dr_valid = np.where(valid, dr0, np.nan)
    if robust_cut:
        # Robust rejection pairs a robust SCALE (MAD-σ, plume-insensitive) with a
        # robust LOCATION (the median): c_initial is slightly plume-biased, so the
        # background sits a hair off zero, and a tiny MAD-σ around zero would miss
        # it. Centering on the median keeps the background and excludes the plume.
        # Median over the finite values directly (== nanmedian) so a fully-masked
        # chip doesn't emit an all-NaN-slice warning; the σ guard handles it below.
        finite_dr = dr_valid[np.isfinite(dr_valid)]
        center = float(np.median(finite_dr)) if finite_dr.size else 0.0
        sigma = robust_sigma(dr_valid)
    else:
        center = 0.0
        sigma = float(np.nanstd(dr_valid))

    # Drop |ΔR − center| > 1σ so a real plume can't drag the calibration toward
    # itself. Too few surviving background pixels ⇒ keep the initial calibration.
    keep = valid & np.isfinite(dr0) & (np.abs(dr0 - center) <= sigma)
    if not np.isfinite(sigma) or np.count_nonzero(keep) < 3:
        return MbspResult(delta_r=dr0, c=c_initial, c_initial=c_initial, n_excluded=0)

    n_excluded = int(np.count_nonzero(valid & np.isfinite(dr0) & (np.abs(dr0 - center) > sigma)))
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
