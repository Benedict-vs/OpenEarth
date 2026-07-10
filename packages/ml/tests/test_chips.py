"""Offline tests for the chip-export helpers (grid regrid + sample policy)."""

from __future__ import annotations

import numpy as np

from openearth.ee.pixels import grid_for
from openearth.geometry import BBox
from openearth.methane.plume import pixel_area_m2
from openearth_ml.chips import regrid_mask_nearest, select_export_samples

_BBOX = BBox(53.0, 38.0, 53.04, 38.03)


def test_regrid_mask_preserves_area_within_tolerance() -> None:
    src = grid_for(_BBOX, 10)
    dst = grid_for(_BBOX, 20)
    mask = np.zeros((src.height, src.width), dtype=bool)
    # a solid block in the middle (well away from edges)
    mask[src.height // 4 : src.height // 2, src.width // 4 : src.width // 2] = True

    out = regrid_mask_nearest(mask, src, dst)
    assert out.shape == (dst.height, dst.width)
    src_area = int(mask.sum()) * pixel_area_m2(src)
    dst_area = int(out.sum()) * pixel_area_m2(dst)
    # nearest-neighbour 10→20 m keeps ground area to within one pixel border.
    assert abs(dst_area - src_area) / src_area < 0.15


def test_regrid_mask_empty_stays_empty() -> None:
    src, dst = grid_for(_BBOX, 10), grid_for(_BBOX, 20)
    out = regrid_mask_nearest(np.zeros((src.height, src.width), bool), src, dst)
    assert not out.any()
    assert out.shape == (dst.height, dst.width)


def test_regrid_mask_tolerates_one_pixel_src_shape_mismatch() -> None:
    src, dst = grid_for(_BBOX, 10), grid_for(_BBOX, 20)
    mask = np.ones((src.height - 1, src.width - 1), dtype=bool)  # stored mask 1px smaller
    out = regrid_mask_nearest(mask, src, dst)  # must not index out of bounds
    assert out.shape == (dst.height, dst.width)
    assert out.all()


def _tile(site: str, positive: bool, usable: bool) -> dict:
    return {"site_id": site, "positive": positive, "usable": usable}


def test_select_samples_balances_negatives_per_site() -> None:
    tiles: dict[str, dict] = {}
    # site A: 20 usable positives, 100 usable negatives (2× = 40 > floor)
    for i in range(20):
        tiles[f"train/A{i}"] = _tile("A", True, True)
    for i in range(100):
        tiles[f"train/An{i}"] = _tile("A", False, True)
    # site B: 0 positives, 50 negatives (must still contribute the floor)
    for i in range(50):
        tiles[f"train/Bn{i}"] = _tile("B", False, True)
    # a non-usable positive must be dropped
    tiles["train/drop"] = _tile("A", True, False)

    chosen = set(select_export_samples(tiles, neg_per_pos=2.0, min_neg_per_site=25, seed=0))
    a_pos = sum(1 for k in chosen if k.startswith("train/A") and not k.startswith("train/An"))
    a_neg = sum(1 for k in chosen if k.startswith("train/An"))
    b_neg = sum(1 for k in chosen if k.startswith("train/Bn"))
    assert a_pos == 20  # all usable positives kept
    assert a_neg == 40  # 2× positives (above the floor)
    assert b_neg == 25  # positive-free site still gets the floor
    assert "train/drop" not in chosen  # non-usable dropped


def test_select_samples_deterministic() -> None:
    tiles = {f"train/An{i}": _tile("A", False, True) for i in range(100)}
    tiles.update({f"train/A{i}": _tile("A", True, True) for i in range(5)})
    a = select_export_samples(tiles, seed=1)
    b = select_export_samples(tiles, seed=1)
    assert a == b
