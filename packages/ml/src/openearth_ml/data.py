"""Chip dataset, per-site folds, and normalisation stats for training.

Reads the ``.npz`` chips written by ``scripts/export_ch4net_chips.py`` and their
manifest. Splitting is **GroupKFold by site** — all 23 sites are Turkmenistan
O&G, so a random split would leak surface texture between train and val;
holding out whole sites is the only honest estimate. Augmentation is D4 only
(the 8 flips/rot90) — no photometric jitter, since the channels are physical.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from numpy.typing import NDArray
from torch.utils.data import Dataset

from openearth.methane.channels import CHANNELS, ChannelStats, normalize

# Square so any D4 rotation of a (≤114 × ≤92) 20 m chip still fits; a multiple of
# 32 so the 5-stage U-Net's skip connections line up.
INPUT_HW = (128, 128)


@dataclass(frozen=True)
class ChipRef:
    path: Path
    site_id: str
    split: str
    positive: bool


def load_refs(chips_dir: Path) -> list[ChipRef]:
    """Every successfully-exported chip, from the manifest (status == ok)."""
    manifest = json.loads((chips_dir / "manifest.json").read_text())
    refs: list[ChipRef] = []
    for key, m in manifest.items():
        if m.get("status") != "ok":
            continue
        path = chips_dir / f"{key}.npz"
        if path.exists():
            refs.append(
                ChipRef(path, m["site_id"], m.get("split", key.split("/")[0]), bool(m["positive"]))
            )
    return sorted(refs, key=lambda r: str(r.path))


def site_folds(
    refs: list[ChipRef], n_splits: int = 5
) -> tuple[list[tuple[list[int], list[int]]], dict[str, int]]:
    """GroupKFold by site: each site lands in exactly one validation fold.

    Sites are assigned largest-first, round-robin, so folds are size-balanced.
    Returns per-fold (train_idx, val_idx) and the site→fold map.
    """
    counts = Counter(r.site_id for r in refs)
    ordered = sorted(counts, key=lambda s: (-counts[s], s))
    fold_of = {site: i % n_splits for i, site in enumerate(ordered)}
    folds: list[tuple[list[int], list[int]]] = []
    for f in range(n_splits):
        val = [i for i, r in enumerate(refs) if fold_of[r.site_id] == f]
        train = [i for i, r in enumerate(refs) if fold_of[r.site_id] != f]
        folds.append((train, val))
    return folds, fold_of


def compute_channel_stats(
    refs: list[ChipRef], *, per_chip: int = 512, seed: int = 0
) -> ChannelStats:
    """Robust per-channel median/MAD over a finite-pixel subsample of the chips."""
    rng = np.random.default_rng(seed)
    pools: list[list[NDArray[np.float64]]] = [[] for _ in CHANNELS]
    for ref in refs:
        ch = np.load(ref.path)["channels"].astype(np.float64)  # (H, W, 5)
        for i in range(len(CHANNELS)):
            v = ch[..., i].ravel()
            v = v[np.isfinite(v)]
            if v.size:
                pools[i].append(rng.choice(v, size=min(per_chip, v.size), replace=False))
    median: list[float] = []
    mad: list[float] = []
    for i in range(len(CHANNELS)):
        allv = np.concatenate(pools[i]) if pools[i] else np.zeros(1)
        m = float(np.median(allv))
        median.append(m)
        mad.append(float(np.median(np.abs(allv - m))))
    return ChannelStats(CHANNELS, tuple(median), tuple(mad))


def _augment_d4(
    ch: NDArray[np.float32], mask: NDArray[np.float32], k: int
) -> tuple[NDArray, NDArray]:
    rot = k % 4
    ch = np.rot90(ch, rot, axes=(0, 1))
    mask = np.rot90(mask, rot)
    if k >= 4:
        ch = ch[:, ::-1]
        mask = mask[:, ::-1]
    return np.ascontiguousarray(ch), np.ascontiguousarray(mask)


def _fit_to(arr: NDArray[np.float32], hw: tuple[int, int]) -> NDArray[np.float32]:
    """Zero-pad (bottom/right) or centre-crop the H, W axes to exactly *hw*."""
    h, w = arr.shape[:2]
    th, tw = hw
    r0 = max(0, (h - th) // 2)
    c0 = max(0, (w - tw) // 2)
    arr = arr[r0 : r0 + min(h, th), c0 : c0 + min(w, tw)]
    pad = [(0, th - arr.shape[0]), (0, tw - arr.shape[1])] + [(0, 0)] * (arr.ndim - 2)
    return np.pad(arr, pad, mode="constant")


class ChipDataset(Dataset):
    """(channels, mask) tensors: normalise → optional D4 → fit to INPUT_HW."""

    def __init__(self, refs: list[ChipRef], stats: ChannelStats, *, augment: bool, seed: int = 0):
        self.refs = refs
        self.stats = stats
        self.augment = augment
        self.rng = np.random.default_rng(seed)

    def __len__(self) -> int:
        return len(self.refs)

    def __getitem__(self, i: int) -> tuple[torch.Tensor, torch.Tensor]:
        ref = self.refs[i]
        z = np.load(ref.path)
        ch = normalize(z["channels"], self.stats)  # (H, W, 5) float32, NaN→0
        mask = z["mask"].astype(np.float32)
        if self.augment:
            ch, mask = _augment_d4(ch, mask, int(self.rng.integers(0, 8)))
        ch = _fit_to(ch, INPUT_HW)
        mask = _fit_to(mask, INPUT_HW)
        x = torch.from_numpy(np.ascontiguousarray(ch)).permute(2, 0, 1)  # (5, H, W)
        y = torch.from_numpy(np.ascontiguousarray(mask))[None]  # (1, H, W)
        return x, y
