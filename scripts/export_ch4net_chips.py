#!/usr/bin/env python
"""Rebuild CH4Net training chips through our own 20 m GEE pipeline (live, resumable).

For each selected tile the recovered metadata (site, date, bbox — see
``scripts/recover_ch4net_metadata.py``) drives our *own* pipeline, so training
sees exactly what ``/methane/ml/scan`` sees at serve time:

    resolve the target S2 scene on the recovered date → pick an MBMP reference
    via our own ``pick_reference`` → ``fetch_chip`` target + reference at 20 m →
    ``build_channels`` → regrid the CH4Net 10 m mask onto our 20 m grid.

One ``.npz`` (channels + mask) per sample under ``data_dir/ml/ch4net/chips/`` plus
a manifest recording per-sample status (ok / no-scene / ref-fail / cloud-fail /
error) and provenance. Resumable (existing npz skipped); all EE round-trips go
through ``ee_call`` inside ``list_scenes`` / ``fetch_chip``.

LICENSE WALL: chips are a CH4Net derivative → they live under the git-ignored
data_dir and are NEVER committed. Manual step, never in CI:

    uv run python scripts/export_ch4net_chips.py            # full export
    uv run python scripts/export_ch4net_chips.py --limit 20 # pilot
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

import numpy as np

from openearth.ee import client
from openearth.ee.pixels import grid_for
from openearth.geometry import BBox
from openearth.methane.channels import build_channels
from openearth.methane.retrieval import fetch_chip
from openearth.methane.scenes import S2Scene, list_scenes, pick_reference
from openearth.settings import get_settings
from openearth_ml.chips import regrid_mask_nearest, select_export_samples

_settings = get_settings()
CH4NET = _settings.data_dir / "ml" / "ch4net"
META_PATH = CH4NET / "recovery" / "metadata.json"
CHIPS_DIR = CH4NET / "chips"
SCALE_M = 20
REF_WINDOW_DAYS = 150
CLOUD_FRACTION_MAX = 0.5  # reject a target chip more than half masked


def _load_mask(key: str) -> np.ndarray:
    split, idx = key.split("/")
    return np.asarray(np.load(CH4NET / "raw" / split / "label" / f"{idx}.npy")).astype(bool)


def _target_scene(bbox: BBox, day: date) -> S2Scene | None:
    """The S2 acquisition on *day* over *bbox* (least cloudy if the orbit repeats)."""
    nxt = (day + timedelta(days=1)).isoformat()
    scenes = list_scenes(bbox, day.isoformat(), nxt, max_cloud=100)
    same_day = [s for s in scenes if s.time.date() == day]
    return min(same_day, key=lambda s: s.cloud_pct) if same_day else None


def export_one(key: str, meta: dict) -> tuple[str, dict, np.ndarray | None, np.ndarray | None]:
    """Rebuild one sample. Returns (status, provenance, channels, mask)."""
    w, s, e, n = meta["bbox"]
    bbox = BBox(w, s, e, n)
    day = date.fromisoformat(meta["date"])

    target = _target_scene(bbox, day)
    if target is None:
        return "no-scene", {}, None, None
    window = list_scenes(
        bbox,
        (day - timedelta(days=REF_WINDOW_DAYS)).isoformat(),
        (day + timedelta(days=REF_WINDOW_DAYS)).isoformat(),
        max_cloud=60,
    )
    reference = pick_reference(target, window)
    if reference is None:
        return "ref-fail", {"target_scene": target.scene_id}, None, None

    target_chip = fetch_chip(target, bbox, scale_m=SCALE_M)
    if float(np.isnan(target_chip.bands["B11"]).mean()) > CLOUD_FRACTION_MAX:
        return "cloud-fail", {"target_scene": target.scene_id}, None, None
    reference_chip = fetch_chip(reference, bbox, scale_m=SCALE_M)

    channels = build_channels(target_chip, reference_chip)
    mask = regrid_mask_nearest(_load_mask(key), grid_for(bbox, 10), target_chip.grid)
    prov = {
        "target_scene": target.scene_id,
        "reference_scene": reference.scene_id,
        "site_id": meta["site_id"],
        "date": meta["date"],
        "split": key.split("/")[0],
        "positive": bool(meta["positive"]),
        "shape": list(channels.shape),
    }
    return "ok", prov, channels, mask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neg-per-pos", type=float, default=2.0)
    parser.add_argument("--min-neg-per-site", type=int, default=25)
    parser.add_argument("--limit", type=int, default=None, help="export only the first N (pilot)")
    parser.add_argument("--sites", default=None, help="comma-separated site ids to restrict")
    args = parser.parse_args()

    if not META_PATH.exists():
        sys.exit(f"{META_PATH} missing — run scripts/recover_ch4net_metadata.py first.")
    tiles: dict[str, dict] = json.loads(META_PATH.read_text())["tiles"]
    keys = select_export_samples(
        tiles, neg_per_pos=args.neg_per_pos, min_neg_per_site=args.min_neg_per_site
    )
    if args.sites:
        want = {s.strip() for s in args.sites.split(",")}
        keys = [k for k in keys if tiles[k]["site_id"] in want]
    if args.limit:
        keys = keys[: args.limit]
    print(f"exporting {len(keys)} chips at {SCALE_M} m → {CHIPS_DIR}")

    client.initialize()
    manifest_path = CHIPS_DIR / "manifest.json"
    CHIPS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    )

    counts: dict[str, int] = {}
    for i, key in enumerate(keys):
        out = CHIPS_DIR / f"{key}.npz"
        if out.exists():
            counts["skip"] = counts.get("skip", 0) + 1
            continue
        try:
            status, prov, channels, mask = export_one(key, tiles[key])
        except Exception as exc:  # per-sample fault isolation — record and continue
            status = f"error:{type(exc).__name__}"
            prov, channels, mask = {"error": str(exc)}, None, None
        counts[status] = counts.get(status, 0) + 1
        manifest[key] = {"status": status, **prov}
        if status == "ok" and channels is not None and mask is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(out, channels=channels, mask=mask)
        if (i + 1) % 50 == 0 or i + 1 == len(keys):
            manifest_path.write_text(json.dumps(manifest))
            print(f"  {i + 1}/{len(keys)}  {counts}")
    manifest_path.write_text(json.dumps(manifest))
    print(f"done: {counts}")
    print("  (chips + manifest are CH4Net derivatives — never commit; under data_dir/ml)")


if __name__ == "__main__":
    main()
