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
from itertools import pairwise
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
    # Whether a given source pixel's window holds the kept plume: True = source-anchored,
    # False = the source window was empty and the peak-blob fallback chose the mask
    # (it may lie anywhere in the chip), None = no source given or nothing detected.
    source_hit: bool | None = None


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
) -> tuple[list[int], bool | None]:
    """Pick the label ids that make up the plume, and whether the source anchored them.

    If *source_rc* is given, keep every valid component intersecting a 7×7
    window around it; otherwise (or if none intersect) keep the single valid
    component containing the peak-enhancement pixel. The second value is True
    for a source-anchored pick, False when a given source's window was empty and
    the peak fallback chose a component elsewhere, None when no source was given.
    """
    if source_rc is not None:
        r, c = source_rc
        r0, r1 = max(0, r - _SOURCE_WINDOW), r + _SOURCE_WINDOW + 1
        c0, c1 = max(0, c - _SOURCE_WINDOW), c + _SOURCE_WINDOW + 1
        window = labels[r0:r1, c0:c1]
        hit = [i for i in np.unique(window) if int(i) in valid_ids]
        if hit:
            return [int(i) for i in hit], True

    # Peak-enhancement fallback: the component holding the largest field value
    # among the valid components.
    in_valid = np.isin(labels, valid_ids)
    scored = np.where(in_valid & np.isfinite(field), field, -np.inf)
    peak_label = int(labels[np.unravel_index(int(np.argmax(scored)), scored.shape)])
    return [peak_label], (False if source_rc is not None else None)


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

    keep, source_hit = _select_components(labels, field, valid_ids, source_rc)
    mask = np.isin(labels, keep)
    n_pixels = int(mask.sum())
    return PlumeMask(
        mask=mask,
        sigma=sigma,
        k_sigma=k_sigma,
        n_pixels=n_pixels,
        area_m2=n_pixels * pixel_area_m2(grid),
        source_hit=source_hit,
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


def _ring_area_centroid(
    ring: list[list[float]], origin: tuple[float, float]
) -> tuple[float, float, float]:
    """Signed shoelace area and centroid (x, y) of one closed ring, relative to *origin*.

    Shifting to a local origin matters: with absolute degrees (~54, ~38) the
    cross products are ~10⁸× a plume's area and cancel catastrophically.
    """
    ox, oy = origin
    pts = [(x - ox, y - oy) for x, y in ring]
    area2 = cx = cy = 0.0
    for (x0, y0), (x1, y1) in pairwise(pts):
        cross = x0 * y1 - x1 * y0
        area2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if area2 == 0.0:
        return 0.0, 0.0, 0.0
    return area2 / 2.0, cx / (3.0 * area2), cy / (3.0 * area2)


def mask_centroid(mask_geojson: dict[str, Any] | None) -> tuple[float, float] | None:
    """Area-weighted (lat, lon) centroid of a :func:`mask_outline_geojson` outline.

    This is where the plume IS — the place a coincidence match (EMIT, reference
    events) must measure from. The chip centre can sit up to ~14 km away on a
    20 km chip, beyond a 5 km match radius. Holes subtract (their signed area is
    opposite to the shell's). Planar in degrees: the components span ≤ 20 km, so
    the cos-latitude distortion is uniform across them. None for an empty mask.
    """
    if not mask_geojson:
        return None
    total = sx = sy = 0.0
    origin: tuple[float, float] | None = None
    for feature in mask_geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        polygons = geometry.get("coordinates", [])
        if geometry.get("type") == "Polygon":
            polygons = [polygons]
        for polygon in polygons:
            shell_sign = 0.0
            for ring in polygon:
                if origin is None:
                    origin = (float(ring[0][0]), float(ring[0][1]))
                area, cx, cy = _ring_area_centroid(ring, origin)
                if shell_sign == 0.0:
                    shell_sign = 1.0 if area >= 0 else -1.0
                # Normalise orientation: shell positive, holes negative.
                weight = area * shell_sign
                total += weight
                sx += weight * cx
                sy += weight * cy
    if total <= 0.0 or origin is None:
        return None
    return (origin[1] + sy / total, origin[0] + sx / total)


def distance_to_mask_m(
    mask: NDArray[np.bool_], grid: GridSpec, lon: float, lat: float
) -> float | None:
    """Ground distance (m) from (lon, lat) to the nearest masked pixel centre.

    Records *how far* a reported plume is from a known source — a mask 80 m
    downwind and one 3 km away are both "not touching" the source window, but
    only one can be that source's plume. Local equirectangular metres (chips
    are ≤ 20 km). None for an empty mask.
    """
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return None
    px_lon = grid.x0 + (cols + 0.5) * grid.xscale
    px_lat = grid.y0 - (rows + 0.5) * grid.yscale
    dx = (px_lon - lon) * _M_PER_DEG * np.cos(np.radians(lat))
    dy = (px_lat - lat) * _M_PER_DEG
    return float(np.hypot(dx, dy).min())
