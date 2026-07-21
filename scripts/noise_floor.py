#!/usr/bin/env python
"""Per-site empirical noise floor for the methane pipeline (Tier 1 fix 1).

Runs the *identical* detection (``analyze``, default k·σ = 2, min_area 5, seeded
MC) on presumed-plume-free scene pairs at each seeded site, over a 10 km analysis
area — the scale users actually analyse. Any component it "detects" on a random
pair is retrieval noise (or, at a recurrent emitter, residual real emission — so
the floor is an *upper bound on trustworthiness*, the conservative direction).

Live EE, run manually with real auth (never in CI):

    OPENEARTH_EE_TESTS=1 uv run python scripts/noise_floor.py            # print only
    OPENEARTH_EE_TESTS=1 uv run python scripts/noise_floor.py --freeze   # write v2 JSON

``--freeze`` writes ``packages/api/src/openearth_api/data/noise_floor_v2.json``
(packaged, served by the API without scripts/ at runtime). The floor is versioned
and append-only: Phase 9's ALGO-7 bundle (robust-σ refit + NHI exclusion) shifts
the retrieved noise Qs, so this rerun writes **v2** beside the untouched
``noise_floor_v1.json`` (LUT v5, ALGO 6) and the service loader is bumped to v2.
Schema:

    {
      "version": 2,
      "provenance": {git_hash, lut_version, run_utc, window, n_targets_per_site},
      "detect_params": {k_sigma, min_area_px, mc_seed, mc_n},
      "method_note": "...", "honesty_note": "...",
      "sites": {"<site name>": {n_pairs, detect_rate, q_noise_kg_h:[...], floor_kg_h}},
      "global": {floor_kg_h, n_detected}
    }

``floor_kg_h`` per site = median of the *detected* noise Qs; ``global.floor_kg_h``
= pooled median across all sites' detected noise Qs. Never mutate an existing
version in place — each rerun writes the next ``noise_floor_vN.json`` + a loader
constant bump (baseline discipline).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from openearth.catalog.presets import METHANE_SITES
from openearth.ee.client import initialize
from openearth.geometry import BBox
from openearth.methane.conversion import load_lut
from openearth.methane.detect import analyze
from openearth.methane.ime import McParams
from openearth.methane.scenes import S2Scene, list_scenes

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Phase 9 ALGO-7 re-freeze: v2 beside the untouched v1 (append-only lineage).
_FLOOR_VERSION = 2
_OUT_PATH = (
    _REPO_ROOT
    / "packages"
    / "api"
    / "src"
    / "openearth_api"
    / "data"
    / f"noise_floor_v{_FLOOR_VERSION}.json"
)

_ANALYSIS_KM = 10.0  # the Lab's default analysis-area scale
_KM_PER_DEG = 111.32
_WINDOW = ("2022-01-01", "2024-01-01")  # fixed 2-year window
_N_TARGETS = 5
_MAX_CLOUD = 30.0
_MC = McParams(n=500, seed=0)
_K_SIGMA = 2.0
_MIN_AREA_PX = 5


def _analysis_bbox(site_bbox: BBox, size_km: float = _ANALYSIS_KM) -> BBox:
    """A *size_km* square centred on *site_bbox* — matches the web's default area."""
    lat, lon = site_bbox.center
    half_lat = (size_km / 2.0) / _KM_PER_DEG
    half_lon = (size_km / 2.0) / (_KM_PER_DEG * math.cos(math.radians(lat)))
    return BBox(lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat)


def _site_key(full_name: str) -> str:
    """The DB Site.name (the seeded prefix is stripped) — the floor's join key."""
    return full_name.removeprefix("CH4: ")


def _pick_targets(scenes: list[S2Scene], n: int, seed: int) -> list[S2Scene]:
    """Seeded pick of *n* target scenes from the clear-enough list (stable order)."""
    if len(scenes) <= n:
        return scenes
    rng = np.random.default_rng(seed)
    idx = sorted(int(i) for i in rng.choice(len(scenes), size=n, replace=False))
    return [scenes[i] for i in idx]


def _run_pair(bbox: BBox, target: S2Scene) -> dict[str, object]:
    """Run the full detection on one plume-free pair (auto reference); one row."""
    row: dict[str, object] = {
        "target_scene_id": target.scene_id,
        "reference_scene_id": None,
        "no_plume": True,
        "q_kg_h": None,
        "q_sigma_kg_h": None,
        "n_px": 0,
        "u10_ms": None,
        "flags": [],
    }
    try:
        result = analyze(
            bbox,
            target.scene_id,
            method="mbmp",
            k_sigma=_K_SIGMA,
            min_area_px=_MIN_AREA_PX,
            mc=_MC,
        )
    except Exception as exc:  # a failed pair is recorded, never fatal
        row["flags"] = [f"analyze_failed: {exc}"]
        return row
    row["reference_scene_id"] = result.reference.scene_id if result.reference else None
    row["flags"] = list(result.flags)
    row["u10_ms"] = round(float(result.emission.u10_ms), 3)
    if "no_plume" in result.flags or not np.isfinite(result.emission.q_kg_h):
        return row
    row["no_plume"] = False
    row["q_kg_h"] = round(float(result.emission.q_kg_h), 2)
    row["q_sigma_kg_h"] = round(float(result.emission.q_sigma_kg_h), 2)
    row["n_px"] = int(result.plume.n_pixels)
    return row


def run_instrument() -> dict[str, object]:
    initialize()
    lut = load_lut()
    sites: dict[str, object] = {}
    all_detected: list[float] = []
    for full_name, site in METHANE_SITES.items():
        bbox = _analysis_bbox(site.bbox)
        scenes = list_scenes(bbox, *_WINDOW, max_cloud=_MAX_CLOUD)
        targets = _pick_targets(scenes, _N_TARGETS, _MC.seed)
        rows = [_run_pair(bbox, t) for t in targets]
        detected = [float(r["q_kg_h"]) for r in rows if r["q_kg_h"] is not None]
        all_detected.extend(detected)
        key = _site_key(full_name)
        sites[key] = {
            "n_pairs": len(rows),
            "detect_rate": round(len(detected) / len(rows), 3) if rows else 0.0,
            "q_noise_kg_h": sorted(round(q, 2) for q in detected),
            "floor_kg_h": round(float(np.median(detected)), 2) if detected else None,
            "pairs": rows,
        }
        print(
            f"{key:<28} pairs={len(rows)} detected={len(detected)} floor={sites[key]['floor_kg_h']}"
        )

    global_floor = round(float(np.median(all_detected)), 2) if all_detected else None
    return {
        "version": _FLOOR_VERSION,
        "provenance": {
            "git_hash": _git_hash(),
            "lut_version": lut.version,
            "run_utc": datetime.now(UTC).isoformat(),
            "window": list(_WINDOW),
            "n_targets_per_site": _N_TARGETS,
            "max_cloud_pct": _MAX_CLOUD,
        },
        "detect_params": {
            "k_sigma": _K_SIGMA,
            "min_area_px": _MIN_AREA_PX,
            "mc_seed": _MC.seed,
            "mc_n": _MC.n,
        },
        "method_note": (
            "The identical `analyze` (MBMP, k·σ=2, min_area 5, seeded MC) run on "
            "seeded-random presumed-plume-free scene pairs over a 10 km analysis area "
            "per seeded site. floor_kg_h = median of the DETECTED noise Qs."
        ),
        "honesty_note": (
            "At recurrent emitters the floor includes real residual emission, so it is "
            "an UPPER BOUND on trustworthiness (the conservative direction for a floor). "
            "detect_rate reports how many plume-free pairs 'detected' anything — a site "
            "at 5/5 reads as exactly that."
        ),
        "sites": sites,
        "global": {"floor_kg_h": global_floor, "n_detected": len(all_detected)},
    }


def _git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--freeze", action="store_true", help=f"write the packaged v{_FLOOR_VERSION} floor JSON"
    )
    args = parser.parse_args()

    floor = run_instrument()
    g = floor["global"]  # type: ignore[index]
    print(f"\nglobal floor_kg_h = {g['floor_kg_h']}  (n_detected = {g['n_detected']})")

    if args.freeze:
        _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_OUT_PATH, "w") as fh:
            json.dump(floor, fh, indent=2, ensure_ascii=False)
        print(f"froze floor → {_OUT_PATH.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
