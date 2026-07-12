"""Dataset, fold, stats, and spatial-clustering tests (synthetic chips)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openearth.methane.channels import CHANNELS
from openearth_ml.data import (
    INPUT_HW,
    ChipDataset,
    ChipRef,
    _chip_key,
    _fit_to,
    assert_no_fold_overlap,
    cluster_folds,
    compute_channel_stats,
    load_refs,
    site_folds,
)


def _geo_for(refs: list[ChipRef], centers: dict, half: float = 0.005) -> dict[str, dict]:
    """Synthetic geolocation: each site at a centre, a chip bbox ±*half* deg around it."""
    geo: dict[str, dict] = {}
    for r in refs:
        lon, lat = centers[r.site_id]
        geo[_chip_key(r)] = {
            "site_id": r.site_id,
            "center_lon": lon,
            "center_lat": lat,
            "bbox": [lon - half, lat - half, lon + half, lat + half],
        }
    return geo


def test_site_folds_never_leak_a_site(chips_dir: Path) -> None:
    refs = load_refs(chips_dir)
    folds, fold_of = site_folds(refs, n_splits=4)
    assert len(folds) == 4
    for train_idx, val_idx in folds:
        train_sites = {refs[i].site_id for i in train_idx}
        val_sites = {refs[i].site_id for i in val_idx}
        assert train_sites.isdisjoint(val_sites)  # no site in both
    # every site is validated exactly once across folds
    assert set(fold_of) == {"S1", "S2", "S3", "S4"}


def test_cluster_folds_merge_nearby_sites(chips_dir: Path) -> None:
    """Sites within 5 km land in one cluster → one fold (fix 6 / Tier 2 F2)."""
    refs = load_refs(chips_dir)
    # S1,S2 ~0.9 km apart; S3, S4 far (>80 km).
    centers = {"S1": (54.0, 39.0), "S2": (54.01, 39.0), "S3": (55.0, 39.0), "S4": (56.0, 39.0)}
    geo = _geo_for(refs, centers)
    folds, fold_of_site, site_cluster = cluster_folds(refs, geo, n_splits=3, cluster_km=5.0)
    assert site_cluster["S1"] == site_cluster["S2"]  # merged
    assert site_cluster["S3"] != site_cluster["S1"]
    assert fold_of_site["S1"] == fold_of_site["S2"]  # same fold
    assert assert_no_fold_overlap(refs, geo, folds) == 0  # clustering separated the folds


def test_assert_no_fold_overlap_raises_on_leak(chips_dir: Path) -> None:
    """A hand-made fold that splits fully-overlapping chips must abort (fix 6 guard)."""
    refs = load_refs(chips_dir)
    centers = {s: (54.0, 39.0) for s in ("S1", "S2", "S3", "S4")}  # all co-located
    geo = _geo_for(refs, centers)
    half = len(refs) // 2
    bad_folds = [([], list(range(half))), ([], list(range(half, len(refs))))]
    with pytest.raises(RuntimeError, match="overlap"):
        assert_no_fold_overlap(refs, geo, bad_folds)


def test_fit_to_reflect_pads_not_zeros() -> None:
    """_fit_to reflect-pads (fix 11): an all-ones chip stays all ones (zero-pad wouldn't)."""
    out = _fit_to(np.ones((40, 36, 5), dtype=np.float32), INPUT_HW)
    assert out.shape == (*INPUT_HW, 5)
    assert bool((out == 1.0).all())


def test_compute_channel_stats_shape_and_contract(chips_dir: Path) -> None:
    refs = load_refs(chips_dir)
    stats = compute_channel_stats(refs)
    assert stats.channels == CHANNELS
    assert len(stats.median) == 5
    assert len(stats.mad) == 5


def test_dataset_returns_fixed_size_tensors(chips_dir: Path) -> None:
    refs = load_refs(chips_dir)
    stats = compute_channel_stats(refs)
    ds = ChipDataset(refs, stats, augment=True, seed=1)
    x, y = ds[0]
    assert tuple(x.shape) == (5, *INPUT_HW)
    assert tuple(y.shape) == (1, *INPUT_HW)
    assert x.dtype.is_floating_point


def test_load_refs_skips_failed_status(chips_dir: Path, tmp_path: Path) -> None:
    import json

    manifest = json.loads((chips_dir / "manifest.json").read_text())
    manifest["train/999"] = {
        "status": "cloud-fail",
        "site_id": "S1",
        "split": "train",
        "positive": False,
    }
    (chips_dir / "manifest.json").write_text(json.dumps(manifest))
    refs = load_refs(chips_dir)
    assert all("999" not in str(r.path) for r in refs)
