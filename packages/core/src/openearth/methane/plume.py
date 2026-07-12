"""Plume masking on a 2-D enhancement field.

Works on any ``(H, W)`` field paired with a :class:`GridSpec` — the unit is
whatever the field is in (ΔΩ mol/m² or ΔXCH4 ppb). A robust background σ sets
a ``k·σ`` threshold on the *positive* enhancement tail, connected components are
labeled with 8-connectivity, and one plume is selected (by a source window, or
else the component holding the peak enhancement). Pure NumPy/scipy/rasterio —
mypy strict, no exemptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
from rasterio.features import shapes
from rasterio.transform import Affine
from scipy import ndimage

from openearth.ee.pixels import _M_PER_DEG

if TYPE_CHECKING:
    from openearth.ee.pixels import GridSpec

# 8-connectivity structuring element for labeling and opening.
_CONNECTIVITY_8 = np.ones((3, 3), dtype=bool)
# Half-width of the source-search window (7×7 total around source_rc).
_SOURCE_WINDOW = 3


@dataclass(frozen=True)
class PlumeMask:
    """A boolean plume mask plus the statistics behind it."""

    mask: NDArray[np.bool_]  # (H,W)
    sigma: float  # robust background σ of the field
    k_sigma: float
    n_pixels: int
    area_m2: float


def robust_sigma(field: NDArray[np.float64]) -> float:
    """Robust σ estimate 1.4826·MAD over the finite values (NaN-aware)."""
    finite = field[np.isfinite(field)]
    if finite.size == 0:
        return float("nan")
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    return 1.4826 * mad


def pixel_area_m2(grid: GridSpec) -> float:
    """Ground area of one pixel (m²), cosine-corrected at the grid's center latitude."""
    center_lat = grid.y0 - 0.5 * grid.height * grid.yscale
    dx_m = grid.xscale * _M_PER_DEG * float(np.cos(np.radians(center_lat)))
    dy_m = grid.yscale * _M_PER_DEG
    return dx_m * dy_m


def _select_components(
    labels: NDArray[np.intp],
    field: NDArray[np.float64],
    valid_ids: list[int],
    source_rc: tuple[int, int] | None,
) -> list[int]:
    """Pick the label ids that make up the plume.

    If *source_rc* is given, keep every valid component intersecting a 7×7
    window around it; otherwise (or if none intersect) keep the single valid
    component containing the peak-enhancement pixel.
    """
    if source_rc is not None:
        r, c = source_rc
        r0, r1 = max(0, r - _SOURCE_WINDOW), r + _SOURCE_WINDOW + 1
        c0, c1 = max(0, c - _SOURCE_WINDOW), c + _SOURCE_WINDOW + 1
        window = labels[r0:r1, c0:c1]
        hit = [i for i in np.unique(window) if int(i) in valid_ids]
        if hit:
            return [int(i) for i in hit]

    # Peak-enhancement fallback: the component holding the largest field value
    # among the valid components.
    in_valid = np.isin(labels, valid_ids)
    scored = np.where(in_valid & np.isfinite(field), field, -np.inf)
    peak_label = int(labels[np.unravel_index(int(np.argmax(scored)), scored.shape)])
    return [peak_label]


def detect_plume(
    field: NDArray[np.float64],
    grid: GridSpec,
    *,
    k_sigma: float = 2.0,
    min_area_px: int = 5,
    opening: bool = True,
    source_rc: tuple[int, int] | None = None,
) -> PlumeMask:
    """Threshold and label the positive enhancement tail into a single plume.

    An empty result (nothing above ``k·σ``, or all components below
    ``min_area_px``) is a valid all-False mask, not an error.
    """
    field = np.asarray(field, dtype=np.float64)
    sigma = robust_sigma(field)

    empty = PlumeMask(
        mask=np.zeros(field.shape, dtype=bool),
        sigma=sigma,
        k_sigma=k_sigma,
        n_pixels=0,
        area_m2=0.0,
    )
    if not np.isfinite(sigma) or sigma == 0.0:
        return empty

    # Threshold about the field median, not zero (fix 4a / Tier 1 F5): σ is
    # MAD-about-the-median, so a non-zero background median (Jensen skew of the
    # convex inverse LUT, bright-side truncation, surface artefacts) would
    # otherwise silently shift the effective k. Now the centre and the scale agree.
    finite = field[np.isfinite(field)]
    median = float(np.median(finite))
    thresh = np.isfinite(field) & (field >= median + k_sigma * sigma)
    if opening:
        thresh = ndimage.binary_opening(thresh, structure=_CONNECTIVITY_8, iterations=1)
    if not thresh.any():
        return empty

    labels, n = ndimage.label(thresh, structure=_CONNECTIVITY_8)
    if n == 0:
        return empty
    sizes = np.bincount(labels.ravel())
    valid_ids = [i for i in range(1, n + 1) if sizes[i] >= min_area_px]
    if not valid_ids:
        return empty

    keep = _select_components(labels, field, valid_ids, source_rc)
    mask = np.isin(labels, keep)
    n_pixels = int(mask.sum())
    return PlumeMask(
        mask=mask,
        sigma=sigma,
        k_sigma=k_sigma,
        n_pixels=n_pixels,
        area_m2=n_pixels * pixel_area_m2(grid),
    )


def mask_outline_geojson(mask: NDArray[np.bool_], grid: GridSpec) -> dict[str, Any]:
    """Vectorize *mask* into a MultiPolygon FeatureCollection (EPSG:4326).

    Rings are pixel-cornered (rasterio's raster-to-vector output); that is
    acceptable for an outline overlay — they are not smoothed.
    """
    transform = Affine(*grid.affine)
    polygons: list[Any] = []
    for geom, value in shapes(mask.astype(np.uint8), transform=transform):
        if value == 1:
            polygons.append(geom["coordinates"])
    if not polygons:
        return {"type": "FeatureCollection", "features": []}
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "MultiPolygon", "coordinates": polygons},
            }
        ],
    }
