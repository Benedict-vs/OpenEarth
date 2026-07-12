"""Chip dataset, spatial-cluster folds, and normalisation stats for training.

Reads the ``.npz`` chips written by ``scripts/export_ch4net_chips.py`` and their
manifest. Splitting is **GroupKFold by site-cluster** (fix 6 / Tier 2 F2): several
CH4Net "sites" are neighbouring pads in the same field, so holding out whole
*sites* still leaks ground — sites within ~5 km are merged into one group before
folding, and a hard assertion aborts if any cross-fold chip pair overlaps > 10 %
ground footprint. Augmentation is D4 only (the 8 flips/rot90) — no photometric
jitter, since the channels are physical.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from numpy.typing import NDArray
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist
from torch.utils.data import Dataset

from openearth.methane.channels import CHANNELS, ChannelStats, normalize

_KM_PER_DEG = 111.32

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


def load_tile_geo(recovery_dir: Path) -> dict[str, dict]:
    """Per-chip geolocation (site_id, center_lon/lat, bbox) from recovery metadata.

    Keyed like the manifest (``"train/1195"``). Git-ignored recovery data — used
    only at train time to build spatial clusters and check fold overlap.
    """
    tiles: dict[str, dict] = json.loads((recovery_dir / "geolocation.json").read_text())["tiles"]
    return tiles


def _chip_key(ref: ChipRef) -> str:
    return f"{ref.split}/{ref.path.stem}"


def site_centroids(refs: list[ChipRef], geo: dict[str, dict]) -> dict[str, tuple[float, float]]:
    """Per-site (lon, lat) centroid = mean of the site's chips' recovered centres."""
    acc: dict[str, list[tuple[float, float]]] = {}
    for r in refs:
        g = geo.get(_chip_key(r))
        if g is not None and g.get("center_lon") is not None:
            acc.setdefault(r.site_id, []).append((g["center_lon"], g["center_lat"]))
    return {
        s: (float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts])))
        for s, pts in acc.items()
    }


def cluster_sites(
    centroids: dict[str, tuple[float, float]], cluster_km: float = 5.0
) -> dict[str, int]:
    """Single-linkage agglomeration of site centroids at *cluster_km* → site → cluster id.

    Distances are an equirectangular km approximation (the sites span < 1°), exact
    enough at the 5 km merge scale. Never hardcode the cluster lists — they are
    derived data (F2's measured merges are the expected outcome, reported in the eval).
    """
    sites = sorted(centroids)
    if len(sites) <= 1:
        return {s: 0 for s in sites}
    pts = np.array([centroids[s] for s in sites], dtype=np.float64)  # (n, 2) lon, lat
    lat_mean = np.radians(float(pts[:, 1].mean()))
    xy_km = np.column_stack([pts[:, 0] * np.cos(lat_mean), pts[:, 1]]) * _KM_PER_DEG
    linkage_z = linkage(pdist(xy_km), method="single")
    labels = fcluster(linkage_z, t=cluster_km, criterion="distance")
    return {s: int(labels[i]) for i, s in enumerate(sites)}


def cluster_folds(
    refs: list[ChipRef], geo: dict[str, dict], n_splits: int = 5, cluster_km: float = 5.0
) -> tuple[list[tuple[list[int], list[int]]], dict[str, int], dict[str, int]]:
    """GroupKFold by site-cluster (fix 6). Sites within *cluster_km* fold together.

    Returns per-fold (train_idx, val_idx), site → fold, and site → cluster id.
    """
    site_cluster = cluster_sites(site_centroids(refs, geo), cluster_km)
    cluster_counts = Counter(site_cluster[r.site_id] for r in refs if r.site_id in site_cluster)
    ordered = sorted(cluster_counts, key=lambda c: (-cluster_counts[c], c))
    fold_of_cluster = {c: i % n_splits for i, c in enumerate(ordered)}
    fold_of_site = {s: fold_of_cluster[c] for s, c in site_cluster.items()}
    folds: list[tuple[list[int], list[int]]] = []
    for f in range(n_splits):
        val = [i for i, r in enumerate(refs) if fold_of_site.get(r.site_id) == f]
        train = [i for i, r in enumerate(refs) if fold_of_site.get(r.site_id) != f]
        folds.append((train, val))
    return folds, fold_of_site, site_cluster


def _bbox_overlap_frac(a: list[float], b: list[float]) -> float:
    """Intersection area / smaller-box area for two ``[w, s, e, n]`` boxes."""
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    smaller = min(area_a, area_b)
    return inter / smaller if smaller > 0 else 0.0


def assert_no_fold_overlap(
    refs: list[ChipRef],
    geo: dict[str, dict],
    folds: list[tuple[list[int], list[int]]],
    max_overlap: float = 0.10,
) -> int:
    """Abort if any cross-fold chip pair overlaps > *max_overlap* ground footprint (fix 6).

    The F2 instrument becomes a train-time guard. Returns the offending-pair count
    (0 on success); raises ``RuntimeError`` otherwise.
    """
    fold_of_ref: dict[int, int] = {}
    for f, (_, val) in enumerate(folds):
        for i in val:
            fold_of_ref[i] = f
    boxed = [
        (i, geo[_chip_key(refs[i])]["bbox"])
        for i in range(len(refs))
        if _chip_key(refs[i]) in geo and geo[_chip_key(refs[i])].get("bbox")
    ]
    bad = 0
    for x in range(len(boxed)):
        i, bi = boxed[x]
        for y in range(x + 1, len(boxed)):
            j, bj = boxed[y]
            if fold_of_ref.get(i) == fold_of_ref.get(j):
                continue
            if _bbox_overlap_frac(bi, bj) > max_overlap:
                bad += 1
    if bad:
        raise RuntimeError(
            f"{bad} cross-fold chip pairs overlap > {max_overlap:.0%} ground footprint — "
            "spatial clustering failed to separate the folds"
        )
    return bad


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
    """Reflect-pad (bottom/right) or centre-crop the H, W axes to exactly *hw*.

    Reflect-pad (not zero-pad) matches the serve path's ``pad_to_multiple`` bottom/right
    convention (fix 11 / Tier 2 F6), so the model sees the same padding at train and
    serve time — one convention end-to-end.
    """
    h, w = arr.shape[:2]
    th, tw = hw
    r0 = max(0, (h - th) // 2)
    c0 = max(0, (w - tw) // 2)
    arr = arr[r0 : r0 + min(h, th), c0 : c0 + min(w, tw)]
    pad = [(0, th - arr.shape[0]), (0, tw - arr.shape[1])] + [(0, 0)] * (arr.ndim - 2)
    return np.pad(arr, pad, mode="reflect")


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
