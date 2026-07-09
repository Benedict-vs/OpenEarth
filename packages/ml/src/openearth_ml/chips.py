"""Pure helpers for the CH4Net chip-rebuild exporter (offline-testable).

The exporter itself (``scripts/export_ch4net_chips.py``) does the Earth-Engine
fetches; the grid math and sampling policy live here so they can be unit-tested
without EE or torch. Nothing here imports torch — it only uses core + NumPy.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

import numpy as np
from numpy.typing import NDArray

from openearth.ee.pixels import GridSpec


def regrid_mask_nearest(
    mask: NDArray[np.bool_], src_grid: GridSpec, dst_grid: GridSpec
) -> NDArray[np.bool_]:
    """Nearest-neighbour resample a boolean mask from *src_grid* onto *dst_grid*.

    Both grids are north-up EPSG:4326 (``GridSpec``); each destination pixel
    centre is mapped to the source pixel whose centre is nearest, clamped to the
    source bounds. Used to bring the CH4Net 10 m annotation onto our 20 m chip
    grid — pure coordinate math, robust to a one-pixel shape mismatch between the
    stored mask and ``grid_for(bbox, 10)``.
    """
    rows = np.arange(dst_grid.height)
    cols = np.arange(dst_grid.width)
    lon = dst_grid.x0 + (cols + 0.5) * dst_grid.xscale
    lat = dst_grid.y0 - (rows + 0.5) * dst_grid.yscale
    src_c = np.rint((lon - src_grid.x0) / src_grid.xscale - 0.5).astype(int)
    src_r = np.rint((src_grid.y0 - lat) / src_grid.yscale - 0.5).astype(int)
    src_c = np.clip(src_c, 0, mask.shape[1] - 1)
    src_r = np.clip(src_r, 0, mask.shape[0] - 1)
    return np.asarray(mask, dtype=bool)[np.ix_(src_r, src_c)]


def select_export_samples(
    tiles: dict[str, dict[str, Any]],
    *,
    neg_per_pos: float = 2.0,
    min_neg_per_site: int = 25,
    seed: int = 0,
) -> list[str]:
    """Pick which tiles to export: every usable positive + a site-balanced set of
    negatives (≈ ``neg_per_pos`` × positives per site, with a floor so
    positive-free sites still contribute negatives for site-held-out CV).

    A tile's ``usable`` flag already encodes the asymmetric date policy (a positive
    needs a confident date; a negative needs any plume-free scene).
    """
    pos_by_site: dict[str, list[str]] = defaultdict(list)
    neg_by_site: dict[str, list[str]] = defaultdict(list)
    for key, m in tiles.items():
        if not m.get("usable") or not m.get("site_id"):
            continue
        (pos_by_site if m.get("positive") else neg_by_site)[m["site_id"]].append(key)

    rng = random.Random(seed)
    chosen: list[str] = []
    for site in sorted(pos_by_site.keys() | neg_by_site.keys()):
        pos = sorted(pos_by_site.get(site, []))
        negs = sorted(neg_by_site.get(site, []))
        rng.shuffle(negs)
        n_neg = min(len(negs), max(round(len(pos) * neg_per_pos), min_neg_per_site))
        chosen.extend(pos)
        chosen.extend(negs[:n_neg])
    return sorted(chosen)
