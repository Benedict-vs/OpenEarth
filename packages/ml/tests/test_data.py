"""Dataset, site-fold, and stats tests (synthetic chips)."""

from __future__ import annotations

from pathlib import Path

from openearth.methane.channels import CHANNELS
from openearth_ml.data import (
    INPUT_HW,
    ChipDataset,
    compute_channel_stats,
    load_refs,
    site_folds,
)


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
