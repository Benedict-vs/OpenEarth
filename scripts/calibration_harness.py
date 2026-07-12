#!/usr/bin/env python
"""Multi-event methane calibration harness (Phase 3.5, Stage 1b).

Runs the physics pipeline (``openearth.methane.detect.analyze``) against every
event in ``scripts/data/calibration_events.json`` and regresses our retrieved
emission rate against the published same-scene Sentinel-2 rate. Unlike
``validate_events.py`` (a fast two-event pass/fail gate that stays untouched),
this is a *regression instrument*: the aggregate slope / ratio / scatter it
produces is what makes Stages 2 and 3 falsifiable.

    OPENEARTH_EE_TESTS=1 uv run python scripts/calibration_harness.py            # print table
    OPENEARTH_EE_TESTS=1 uv run python scripts/calibration_harness.py --freeze   # write baseline
    OPENEARTH_EE_TESTS=1 uv run python scripts/calibration_harness.py --compare  # diff vs baseline

A ``no_plume`` result or any per-event failure is a recorded *exclusion with
reason*, never a crash; the run is healthy when every event yields either a
finite Q or a documented exclusion and at least ``_MIN_QUANTIFIED`` events are
quantified. The baseline JSON stamps its own provenance (LUT version, MC seed,
git hash, run date); CI never executes this — offline tests validate schema and
the aggregate math only. With N ≈ 15 these aggregates are engineering
diagnostics, not hypothesis tests.

Never tighten any threshold toward the external Varon anchor — it stays a
±30 % sanity band. Precision pins are against our own generated baseline only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

from openearth.ee.client import initialize
from openearth.geometry import BBox
from openearth.methane.conversion import load_lut
from openearth.methane.detect import DetectionResult, analyze
from openearth.methane.ime import McParams

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVENTS_PATH = _REPO_ROOT / "scripts" / "data" / "calibration_events.json"

_MC = McParams(n=500, seed=0)
_MIN_QUANTIFIED = 10
# A retrieval whose plume mask is dominated by out-of-validity pixels is outside the
# forward model's range — MBSP surface structure over heterogeneous terrain inverts to
# huge columns. A *documented exclusion*, published-value-blind, never a silent drop or
# crash. See docs/methane_methods.md §8 (MBSP applicability).
#
# Decoupled from the LUT grid edge on purpose (fix 3 / Tier 1 F3): the v5 grid extends to
# 6.0, so "fraction at the grid edge" no longer catches MBSP surface blowups (they now
# invert to large *finite* columns). The bound stays at the old v4 edge so
# campeche/caspian-class exclusions are stable across the grid extension.
MBSP_VALIDITY_DELTA_OMEGA = 3.0  # mol/m²
_INVALID_FRACTION_MAX = 0.20


# ── Aggregate diagnostics (pure — offline unit-tested in test_calibration_events.py) ──


def slope_through_origin(q_ours: np.ndarray, q_pub: np.ndarray) -> float:
    """Least-squares slope of a line through the origin: Σ(qₒ·qₚ) / Σ(qₚ²)."""
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_pub = np.asarray(q_pub, dtype=np.float64)
    denom = float(np.sum(q_pub**2))
    if denom == 0.0:
        return float("nan")
    return float(np.sum(q_ours * q_pub) / denom)


def median_ratio(q_ours: np.ndarray, q_pub: np.ndarray) -> float:
    """median(qₒ / qₚ) — a robust central bias, insensitive to a few outliers."""
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_pub = np.asarray(q_pub, dtype=np.float64)
    return float(np.median(q_ours / q_pub))


def log_scatter(q_ours: np.ndarray, q_pub: np.ndarray) -> float:
    """Robust log-scatter s = 1.4826 · MAD(log10(qₒ / qₚ)) — a spread, not a bias."""
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_pub = np.asarray(q_pub, dtype=np.float64)
    log_ratio = np.log10(q_ours / q_pub)
    mad = float(np.median(np.abs(log_ratio - np.median(log_ratio))))
    return 1.4826 * mad


def theil_sen_slope(q_ours: np.ndarray, q_pub: np.ndarray) -> float:
    """Theil–Sen pairwise-slope estimator — a robustness cross-check on the LSQ slope.

    Median over all pairs i<j of (qₒⱼ − qₒᵢ)/(qₚⱼ − qₚᵢ). Reported, never gated on.
    """
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_pub = np.asarray(q_pub, dtype=np.float64)
    slopes: list[float] = []
    n = len(q_pub)
    for i in range(n):
        for j in range(i + 1, n):
            dx = q_pub[j] - q_pub[i]
            if dx != 0.0:
                slopes.append(float((q_ours[j] - q_ours[i]) / dx))
    if not slopes:
        return float("nan")
    return float(np.median(slopes))


def spearman(q_ours: np.ndarray, q_pub: np.ndarray) -> tuple[float, float]:
    """Spearman rank correlation ρ and its p-value — the per-event *skill* metric the
    review made a first-class diagnostic (ρ = 0.19, p = 0.5, n = 15 on v4). Reported,
    never gated on; NaN for n < 3 (ρ undefined)."""
    if len(q_ours) < 3:
        return float("nan"), float("nan")
    result = spearmanr(q_ours, q_pub)
    return float(result.statistic), float(result.pvalue)


def aggregates(q_ours: list[float], q_pub: list[float]) -> dict[str, float]:
    a = np.asarray(q_ours, dtype=np.float64)
    p = np.asarray(q_pub, dtype=np.float64)
    rho, pval = spearman(a, p)
    return {
        "n_quantified": len(a),
        "slope_through_origin": slope_through_origin(a, p),
        "median_ratio": median_ratio(a, p),
        "log_scatter": log_scatter(a, p),
        "theil_sen_slope": theil_sen_slope(a, p),
        "spearman_rho": rho,
        "spearman_p": pval,
    }


# ── Live run ──


def _load_events() -> list[dict[str, object]]:
    with open(_EVENTS_PATH) as fh:
        return json.load(fh)["events"]  # type: ignore[no-any-return]


def _git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def _inversion_validity_fraction(result: DetectionResult) -> float:
    """Fraction of masked pixels whose reported ΔΩ is beyond the forward model's
    validity range (|ΔΩ| ≥ ``MBSP_VALIDITY_DELTA_OMEGA``) — decoupled from the LUT
    grid edge (fix 3) so the guard survives the v5 grid extension."""
    in_mask = result.delta_omega[result.plume.mask]
    if in_mask.size == 0:
        return 0.0
    return float(np.mean(np.abs(in_mask) >= MBSP_VALIDITY_DELTA_OMEGA))


def run_event(event: dict[str, object]) -> dict[str, object]:
    """Run one event through ``analyze``; return a per-event result row.

    Excludes (with a reason) on ``no_plume``, an out-of-validity mask, a non-finite Q,
    or any exception — never raises, so one bad scene can't sink the whole regression.
    """
    bbox = BBox(*event["bbox"])  # type: ignore[misc]
    src = event.get("source_lonlat")
    source_lonlat = tuple(src) if src else None  # type: ignore[arg-type]
    row: dict[str, object] = {
        "id": event["id"],
        "region": event["region"],
        "method": event["method"],
        "published_q_t_h": event["published_q_t_h"],
        "published_sigma_t_h": event["published_sigma_t_h"],
        "target_scene_id": event["target_scene_id"],
        "reference_scene_id": event.get("reference_scene_id"),
        "q_ours_t_h": None,
        "sigma_ours_t_h": None,
        "invalid_fraction": None,
        "flags": [],
        "excluded": True,
        "exclusion_reason": None,
    }
    try:
        result = analyze(
            bbox,
            str(event["target_scene_id"]),
            reference_scene_id=(
                str(event["reference_scene_id"]) if event.get("reference_scene_id") else None
            ),
            method=str(event["method"]),
            source_lonlat=source_lonlat,  # type: ignore[arg-type]
            mc=_MC,
        )
    except Exception as exc:
        row["exclusion_reason"] = f"analyze failed: {exc}"
        return row
    row["flags"] = list(result.flags)
    q = result.emission.q_kg_h / 1000.0
    if "no_plume" in result.flags or not np.isfinite(q):
        row["exclusion_reason"] = "no_plume" if "no_plume" in result.flags else "non_finite_q"
        return row
    frac = _inversion_validity_fraction(result)
    row["invalid_fraction"] = round(frac, 4)
    if frac > _INVALID_FRACTION_MAX:
        row["exclusion_reason"] = f"excluded_inversion_validity (frac={frac:.2f})"
        return row
    row["q_ours_t_h"] = round(float(q), 4)
    row["sigma_ours_t_h"] = round(float(result.emission.q_sigma_kg_h / 1000.0), 4)
    row["excluded"] = False
    return row


def run_harness() -> dict[str, object]:
    initialize()
    lut = load_lut()
    events = _load_events()
    rows = [run_event(e) for e in events]
    quantified = [r for r in rows if not r["excluded"]]
    agg = aggregates(
        [float(r["q_ours_t_h"]) for r in quantified],  # type: ignore[arg-type]
        [float(r["published_q_t_h"]) for r in quantified],  # type: ignore[arg-type]
    )
    return {
        "schema": 1,
        "lut_version": lut.version,
        "mc_seed": _MC.seed,
        "mc_n": _MC.n,
        "git_hash": _git_hash(),
        "run_utc": datetime.now(UTC).isoformat(),
        "events": rows,
        "aggregates": agg,
    }


def _print_table(baseline: dict[str, object]) -> None:
    print(
        f"LUT v{baseline['lut_version']}  seed={baseline['mc_seed']}  n={baseline['mc_n']}  "
        f"git={baseline['git_hash']}"
    )
    print(f"{'id':<30}{'method':<7}{'pub':>7}{'ours':>9}{'σ':>8}   note")
    print("-" * 90)
    for r in baseline["events"]:  # type: ignore[union-attr]
        if r["excluded"]:
            print(
                f"{r['id']:<30}{r['method']:<7}{r['published_q_t_h']:>7.1f}"
                f"{'—':>9}{'—':>8}   EXCLUDED: {r['exclusion_reason']}"
            )
        else:
            note = ",".join(r["flags"]) or "ok"  # type: ignore[arg-type]
            print(
                f"{r['id']:<30}{r['method']:<7}{r['published_q_t_h']:>7.1f}"
                f"{r['q_ours_t_h']:>9.2f}{r['sigma_ours_t_h']:>8.2f}   {note}"
            )
    print("-" * 90)
    a = baseline["aggregates"]
    print(
        f"n_quantified={a['n_quantified']}  slope={a['slope_through_origin']:.3f}  "
        f"median_ratio={a['median_ratio']:.3f}  log_scatter={a['log_scatter']:.3f}  "
        f"theil_sen={a['theil_sen_slope']:.3f}  "
        f"spearman_rho={a['spearman_rho']:.3f} (p={a['spearman_p']:.3f})"
    )


def _baseline_path(lut_version: str) -> Path:
    return _REPO_ROOT / "scripts" / "data" / f"calibration_baseline_v{lut_version}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--freeze", action="store_true", help="write the baseline for this LUT version"
    )
    group.add_argument(
        "--compare", action="store_true", help="diff a fresh run against the committed baseline"
    )
    args = parser.parse_args()

    baseline = run_harness()
    _print_table(baseline)
    a = baseline["aggregates"]

    if a["n_quantified"] < _MIN_QUANTIFIED:  # type: ignore[operator, index]
        print(f"\nFAIL: only {a['n_quantified']} events quantified (need ≥ {_MIN_QUANTIFIED})")  # type: ignore[index]
        return 1

    path = _baseline_path(str(baseline["lut_version"]))
    if args.freeze:
        with open(path, "w") as fh:
            json.dump(baseline, fh, indent=2)
        print(f"\nfroze baseline → {path.relative_to(_REPO_ROOT)}")
    elif args.compare:
        if not path.exists():
            print(f"\nno committed baseline at {path.relative_to(_REPO_ROOT)}")
            return 1
        with open(path) as fh:
            committed = json.load(fh)
        ca = committed["aggregates"]
        prov = f"git {committed['git_hash']}, LUT v{committed['lut_version']}"
        print(f"\ncompare vs {path.name} ({prov}):")
        for key in (
            "slope_through_origin",
            "median_ratio",
            "log_scatter",
            "theil_sen_slope",
            "spearman_rho",
        ):
            print(
                f"  {key:<22} baseline={ca[key]:.4f}  now={a[key]:.4f}  Δ={a[key] - ca[key]:+.4f}"
            )  # type: ignore[index]
    return 0


if __name__ == "__main__":
    sys.exit(main())
