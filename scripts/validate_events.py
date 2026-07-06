#!/usr/bin/env python
"""Reproduce documented Sentinel-2 super-emitter events (Phase 3 exit gate).

Runs the physics pipeline (``openearth.methane.detect.analyze``) directly against
live Earth Engine for two pinned events and compares our emission estimate to
the published value. Exits non-zero only on a hard FAIL (σ band inconsistent with
the published value); a MARGINAL point estimate whose σ band still overlaps is an
accepted, documented outcome (see docs/methane_methods.md §8).

    OPENEARTH_EE_TESTS=1 uv run python scripts/validate_events.py

Published values verified against Varon et al. 2021 (AMT 14:2771). The preset
date *hints* (2024) are irrelevant here — this script pins the historical event
dates and the exact target/reference scene ids itself.

Notes on method choice (documented in docs/methane_methods.md):
  - Korpezhe is an intermittent source: MBMP against a plume-free different-date
    reference. The auto reference is unusable here (all scenes share orbit 106,
    and the nearest is the same overpass), so the reference is pinned.
  - Hassi Messaoud is a *continuous* blowout: every in-period scene carries the
    plume, so MBMP would cancel it. Single-scene MBSP with the source location
    is used, averaged over ≥ 3 cloud-free scenes.
"""

from __future__ import annotations

import sys

from openearth.ee.client import initialize
from openearth.geometry import BBox
from openearth.methane.detect import DetectionResult, analyze
from openearth.methane.ime import McParams

_MC = McParams(n=500, seed=0)


def _q_th(result: DetectionResult) -> float:
    """Emission rate in t/h (NaN → NaN)."""
    return result.emission.q_kg_h / 1000.0


def _sigma_th(result: DetectionResult) -> float:
    return result.emission.q_sigma_kg_h / 1000.0


# Verdict tiers. A hard FAIL (σ band inconsistent with published) exits non-zero;
# MARGINAL (point estimate outside the ±50 % window but the σ bands still overlap)
# is a documented, accepted outcome — see docs/methane_methods.md §8.
PASS, MARGINAL, FAIL = "PASS", "⚠ MARGINAL", "FAIL"


def korpezhe() -> tuple[float, float, str, str]:
    """Korpezhe 2018-06-19 — published 11.2 ± 5.2 t/h (GHGSat-D: 11.6 ± 8.8).

    PASS: Q ∈ [5.6, 16.8] t/h (published ±50 %) with σ overlapping. This
    intermittent, pinned-reference event sits near a mask-size cliff, so its
    point estimate is LUT-shape sensitive and its MC band is wide (see the
    LUT history note in docs/methane_methods.md §8).
    """
    bbox = BBox(53.94, 38.47, 53.99, 38.51)
    result = analyze(
        bbox,
        "20180619T070619_20180619T071220_T39SYC",
        reference_scene_id="20180624T070621_20180624T071359_T39SYC",
        method="mbmp",
        mc=_MC,
    )
    q, sigma = _q_th(result), _sigma_th(result)
    lo, hi = 5.6, 16.8
    # σ overlap: our [q−σ, q+σ] intersects the published [11.2−5.2, 11.2+5.2].
    overlaps = (q + sigma) >= (11.2 - 5.2) and (q - sigma) <= (11.2 + 5.2)
    if lo <= q <= hi and overlaps:
        verdict = PASS
    elif overlaps:
        verdict = MARGINAL
    else:
        verdict = FAIL
    return q, sigma, verdict, f"published 11.2 ± 5.2 t/h; ±50 % window [{lo}, {hi}], σ overlaps"


def hassi_messaoud() -> tuple[float, float, str, str]:
    """Hassi Messaoud blowout — published mean 9.3 ± 5.5 t/h over 101 plumes.

    Continuous source (Oct 2019 – Aug 2020): single-scene MBSP at the well,
    averaged over 3 cloud-free scenes in Nov 2019 – Jan 2020. PASS: mean within
    ±50 % of 9.3 t/h, i.e. [4.65, 13.95].
    """
    bbox = BBox(5.88, 31.64, 5.93, 31.68)
    well = (5.9053, 31.6585)
    targets = [
        "20191113T102301_20191113T102419_T32SKA",
        "20191208T102319_20191208T102419_T32SKA",
        "20200114T101259_20200114T101254_T32SKA",
    ]
    values: list[float] = []
    for scene_id in targets:
        result = analyze(bbox, scene_id, method="mbsp", source_lonlat=well, mc=_MC)
        values.append(_q_th(result))
    mean = sum(values) / len(values)
    lo, hi = 4.65, 13.95
    detail = "published mean 9.3 ± 5.5 t/h; per-scene " + ", ".join(f"{v:.1f}" for v in values)
    return mean, 0.0, (PASS if lo <= mean <= hi else FAIL), detail


def main() -> int:
    initialize()
    print(f"{'event':<20}{'ours (t/h)':>16}   verdict     notes")
    print("-" * 100)
    any_fail = False
    for name, fn in (("Korpezhe", korpezhe), ("Hassi Messaoud", hassi_messaoud)):
        q, sigma, verdict, detail = fn()
        any_fail = any_fail or verdict == FAIL
        ours = f"{q:.2f} ± {sigma:.2f}" if sigma else f"{q:.2f} (mean)"
        print(f"{name:<20}{ours:>16}   {verdict:<11}   {detail}")
    print("-" * 100)
    print("SOME TARGETS FAILED" if any_fail else "ALL TARGETS WITHIN σ (see MARGINAL notes)")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
