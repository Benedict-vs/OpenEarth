#!/usr/bin/env python
"""Acquire and inventory the CH4Net dataset (Vaughan et al. 2024, AMT).

Two manual, live subcommands (never run in CI):

    uv run --group ml-data python scripts/fetch_ch4net.py download
    uv run --group ml-data python scripts/fetch_ch4net.py inventory

``download`` snapshots the **gated** Hugging Face dataset ``av555/ch4net``
(doi:10.57967/hf/2117) into ``{data_dir}/ml/ch4net/raw/``. ``inventory`` walks
that raw tree — no network — and writes ``{data_dir}/ml/ch4net/inventory.json``.

LICENSE WALL (non-negotiable): CH4Net is **CC-BY-NC-ND 4.0 and gated**. Nothing
derived from it — imagery, masks, rebuilt chips, per-file manifests, trained
weights, the inventory itself — is ever committed or published. Everything lives
under ``{data_dir}`` (git-ignored). The repo keeps only code, configs, and
aggregate metrics/provenance JSON. The HF token stays in the environment / the
git-ignored ``.env``; it is never baked into this file and the gated endpoint is
never retry-looped.

The inventory is the real Stage 0 deliverable: it resolves the three blocking
questions that gate the Stage 1 exporter contracts — (a) the tile extent /
georeferencing, (b) the negative-label convention, (c) scene-ID availability —
measuring them against the downloaded files and the MIT reference loaders in
``github.com/anna-allen/CH4Net`` (``src/loader.py``).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from openearth.settings import get_settings

REPO_ID = "av555/ch4net"
DOI = "10.57967/hf/2117"
LICENSE = "CC-BY-NC-ND-4.0"

# Paths derived from the resolved data_dir (OPENEARTH_DATA_DIR, default ``data``).
_SETTINGS = get_settings()
CH4NET_DIR = _SETTINGS.data_dir / "ml" / "ch4net"
RAW_DIR = CH4NET_DIR / "raw"
INVENTORY_PATH = CH4NET_DIR / "inventory.json"

# CH4Net's MIT loaders (github.com/anna-allen/CH4Net, src/loader.py) describe the
# *preprint/Zenodo* layout (plume_id + date, pos/neg dirs). The published HF
# release measured here is different — see _analyze_splits.
_GITHUB_REF = "github.com/anna-allen/CH4Net src/loader.py (preprint layout — differs from HF)"


def _resolve_token() -> str:
    """HF token from the environment, falling back to the git-ignored .env.

    Never returns a token baked into the repo — only os.environ or the local
    .env (same store pydantic-settings reads). Fails loudly if absent so the
    gated endpoint is never hit tokenless.
    """
    import os

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        return token.strip()
    env_path = Path(".env")
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            m = re.match(r"\s*HF_TOKEN\s*=\s*(.+)\s*$", line)
            if m:
                return m.group(1).strip().strip("'\"")
    sys.exit(
        "HF_TOKEN not found in the environment or .env.\n"
        "  1. Accept the gate at https://huggingface.co/datasets/av555/ch4net\n"
        "  2. Put HF_TOKEN=hf_... in .env (git-ignored) or `export HF_TOKEN=...`\n"
        "Never commit the token."
    )


def download() -> None:
    """Snapshot the gated dataset into RAW_DIR. No retry on the gate — fail clear."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("huggingface_hub not installed — run via `uv run --group ml-data`.")

    token = _resolve_token()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {REPO_ID} (dataset, {LICENSE}, ~9.8 GB) → {RAW_DIR}")
    print("This is a one-time, resumable, manual step. Ctrl-C is safe to resume later.")
    try:
        path = snapshot_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            local_dir=str(RAW_DIR),
            token=token,
        )
    except Exception as exc:  # classify then re-raise-as-exit
        name = type(exc).__name__
        gated = name == "GatedRepoError" or "gated" in str(exc).lower() or "403" in str(exc)
        if gated:
            sys.exit(
                f"\nGated-access error ({name}). The download was NOT retried.\n"
                "Accept the terms while logged in at\n"
                "  https://huggingface.co/datasets/av555/ch4net\n"
                "then re-run. If you have already accepted, check that HF_TOKEN is a\n"
                "token for the same account that accepted the gate."
            )
        raise
    print(f"Done: {path}")
    print("Next: uv run python scripts/fetch_ch4net.py inventory")


# ── Inventory (offline) ──────────────────────────────────────────────────────


def _walk_tree(root: Path) -> tuple[dict[str, dict], int, int]:
    """Per-directory file counts grouped by extension, plus totals."""
    tree: dict[str, dict] = {}
    total_files = 0
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name.startswith(".cache"):
            continue
        rel_dir = str(path.parent.relative_to(root)) or "."
        node = tree.setdefault(rel_dir, {"n_files": 0, "by_ext": Counter()})
        node["n_files"] += 1
        node["by_ext"][path.suffix or "<none>"] += 1
        total_files += 1
        with contextlib.suppress(OSError):
            total_bytes += path.stat().st_size
    for node in tree.values():
        node["by_ext"] = dict(node["by_ext"])
    return tree, total_files, total_bytes


def _sample_arrays(root: Path, limit_per_bucket: int = 3) -> list[dict]:
    """Sample .npy shapes/dtypes/ranges, bucketed by a coarse path signature."""
    buckets: dict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*.npy"):
        sig = re.sub(r"\d+", "#", str(path.relative_to(root)))
        if len(buckets[sig]) < limit_per_bucket:
            buckets[sig].append(path)
    samples: list[dict] = []
    for sig, paths in sorted(buckets.items()):
        for path in paths:
            try:
                arr = np.load(path, mmap_mode="r", allow_pickle=False)
            except Exception as exc:  # record, don't abort
                samples.append({"path": str(path.relative_to(root)), "error": str(exc)})
                continue
            small = arr.size <= 4_000_000
            block = np.asarray(arr) if small else np.asarray(arr).ravel()[:4_000_000]
            uniq = np.unique(block)
            samples.append(
                {
                    "signature": sig,
                    "path": str(path.relative_to(root)),
                    "shape": list(arr.shape),
                    "dtype": str(arr.dtype),
                    "min": float(np.nanmin(block)),
                    "max": float(np.nanmax(block)),
                    "n_unique": int(uniq.size) if uniq.size <= 16 else None,
                    "looks_binary": bool(uniq.size <= 2 and set(uniq.tolist()) <= {0.0, 1.0}),
                }
            )
    return samples


def _analyze_splits(root: Path) -> dict:
    """Characterise the published HF layout: {split}/{modality}/{int}.npy triples.

    Positivity is per-sample (a non-empty ``label`` mask), not a directory. There
    are no dates, site IDs, scene IDs, or georeferencing anywhere in the release —
    files are opaque integer indices — so this records exactly that.
    """
    want = {"train", "val", "test"}
    splits = [d.name for d in sorted(root.iterdir()) if d.is_dir() and d.name in want]
    out: dict[str, dict] = {}
    totals = {"n": 0, "positive": 0}
    for split in splits:
        modalities = {m.name for m in (root / split).iterdir() if m.is_dir()}
        label_dir = root / split / "label"
        n = pos = 0
        dims: set[tuple[int, int]] = set()
        for f in sorted(label_dir.glob("*.npy")):
            arr = np.load(f, mmap_mode="r", allow_pickle=False)
            n += 1
            if np.asarray(arr).any():
                pos += 1
            dims.add((int(arr.shape[0]), int(arr.shape[1])))
        hs = [h for h, _ in dims]
        ws = [w for _, w in dims]
        out[split] = {
            "modalities": sorted(modalities),
            "n_samples": n,
            "n_positive": pos,
            "n_negative": n - pos,
            "h_px_range": [min(hs), max(hs)] if hs else None,
            "w_px_range": [min(ws), max(ws)] if ws else None,
            "filename_scheme": "integer index only (e.g. 0.npy) — no date/site/scene id",
        }
        totals["n"] += n
        totals["positive"] += pos
    out["_totals"] = {**totals, "negative": totals["n"] - totals["positive"]}
    return out


def _metadata_files(root: Path) -> list[dict]:
    """Non-array files that might carry georeferencing / site coords / scene IDs."""
    interesting = {".csv", ".json", ".geojson", ".nc", ".tif", ".tiff", ".txt", ".md", ".yaml"}
    out: list[dict] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() in interesting and not path.name.startswith("."):
            out.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size})
    return out[:50]


def inventory() -> None:
    """Measure the raw tree and answer the three Stage-1 blocking questions."""
    if not RAW_DIR.is_dir() or not any(RAW_DIR.iterdir()):
        sys.exit(f"{RAW_DIR} is empty — run `download` first.")

    tree, total_files, total_bytes = _walk_tree(RAW_DIR)
    samples = _sample_arrays(RAW_DIR)
    splits = _analyze_splits(RAW_DIR)
    meta = _metadata_files(RAW_DIR)

    hw = [tuple(s["shape"][:2]) for s in samples if "shape" in s and len(s["shape"]) >= 2]
    px_m = 10  # CH4Net imagery is Sentinel-Hub interpolated to 10 m
    h_all = [h for h, _ in hw]
    w_all = [w for _, w in hw]

    doc = {
        "generated_at": datetime.now(UTC).isoformat(),
        "repo": REPO_ID,
        "doi": DOI,
        "license": LICENSE,
        "license_wall": "Nothing derived from this dataset is ever committed or published.",
        "github_reference": _GITHUB_REF,
        "raw_dir": str(RAW_DIR),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "top_level_entries": sorted(p.name for p in RAW_DIR.iterdir()),
        "tree": tree,
        "array_samples": samples,
        "layout": {
            "structure": "{split}/{modality}/{int}.npy — aligned (s2, mbmp, label) triples",
            "modalities": {
                "s2": "(H, W, 12) uint8 — 12 raw Sentinel-2 bands, Sentinel-Hub 10 m, /255",
                "mbmp": "(H, W, 4) uint8 — an RGBA *render* of MBMP, NOT a physical ΔR field",
                "label": "(H, W) binary hand-annotated mask (bool / float64 all-zero = negative)",
            },
            "splits": splits,
        },
        "metadata_files": meta,
        "blocking_questions": {
            "tile_extent": {
                "measured_px_h_range": [min(h_all), max(h_all)] if h_all else None,
                "measured_px_w_range": [min(w_all), max(w_all)] if w_all else None,
                "pixel_size_m_assumed": px_m,
                "extent_km_approx": (
                    [round(max(h_all) * px_m / 1000, 2), round(max(w_all) * px_m / 1000, 2)]
                    if h_all
                    else None
                ),
                "answer": (
                    "VARIABLE, not fixed 200x200: ~216-228 x ~165-182 px @10 m (~2.2 x 1.75 km). "
                    "Paper's '0.01 deg' (~1.1 km) does not match; trust the measurement."
                ),
                "resolved_from": "measured",
            },
            "negative_label_convention": {
                "answer": (
                    "Per-sample, NOT a directory: a sample is positive iff its label mask is "
                    "non-empty. No pos/ or neg/ dirs; negatives are empty-mask samples sitting "
                    "in the same {split}/label/ folder."
                ),
                "totals": splits["_totals"],
                "resolved_from": "measured",
            },
            "scene_id_availability": {
                "answer": (
                    "NONE. Files are opaque integer indices (0.npy, 1.npy, ...) with no date, "
                    "site/plume id, S2 product id, lat/lon, or georeferencing anywhere in the "
                    "release. This is WORSE than the plan's assumed 'date+site' case."
                ),
                "resolved_from": "measured",
            },
        },
        "site_coordinates_source": "NOT PRESENT anywhere in the release.",
        "plan_conflict": {
            "severity": "resolved",
            "summary": (
                "The published HF release strips all georeferencing/dates/scene IDs, which would "
                "make the Phase 5 plan's central mechanisms inexecutable as written: (1) chip-"
                "rebuild via fetch_chip needs date+bbox; (2) our physics channels (MBSP/MBMP ΔR "
                "via retrieval.py) need a reference scene; (3) GroupKFold-by-site CV needs site "
                "labels. Zenodo 8267966 (the metadata-rich preprint version) is dead (404)."
            ),
            "resolution": (
                "scripts/recover_ch4net_metadata.py recovers (site, date, bbox) self-service "
                "(cluster → GEE NCC-peak geolocation → per-tile date match); see §9.2 for stats."
            ),
        },
    }

    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(json.dumps(doc, indent=2))
    t = splits["_totals"]
    print(f"Wrote {INVENTORY_PATH} ({total_files} files, {total_bytes / 1e9:.2f} GB)")
    print(f"  samples={t['n']} positive={t['positive']} negative={t['negative']} | integer-id only")
    print("  BLOCKING: no scene/date/site metadata — see plan_conflict in the JSON.")
    print("  (git-ignored — never commit this file or anything under data_dir/ml)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download", help="gated HF snapshot → data_dir/ml/ch4net/raw (manual)")
    sub.add_parser("inventory", help="offline: write data_dir/ml/ch4net/inventory.json")
    args = parser.parse_args()
    {"download": download, "inventory": inventory}[args.cmd]()


if __name__ == "__main__":
    main()
