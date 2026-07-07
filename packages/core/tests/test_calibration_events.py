"""Stage 1a — calibration event set validation (offline, zero Earth Engine).

Guards the committed ``scripts/data/calibration_events.json`` against the
curation criteria in docs/phase3.5-execution-plan.md: same-scene S2 events with
provenance, N ≥ 10, per-site cap, region diversity, valid bboxes.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pytest

from openearth.geometry import BBox
from openearth.methane import conversion

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVENTS_PATH = _REPO_ROOT / "scripts" / "data" / "calibration_events.json"


def _load_harness():  # type: ignore[no-untyped-def]
    path = _REPO_ROOT / "scripts" / "calibration_harness.py"
    spec = importlib.util.spec_from_file_location("calibration_harness", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIN_EVENTS = 10
_MAX_PER_SITE = 3
_MIN_REGIONS = 4
_METHODS = {"mbsp", "mbmp"}
_ID_SOURCE_RE = re.compile(r"id_source=([0-9a-f-]+)")


@pytest.fixture(scope="module")
def events() -> list[dict[str, object]]:
    with open(_EVENTS_PATH) as fh:
        data = json.load(fh)
    assert data["version"] == 1
    return data["events"]  # type: ignore[no-any-return]


def _site_key(event: dict[str, object]) -> str:
    """A stable per-source key: the MARS id_source when present, else the event id."""
    match = _ID_SOURCE_RE.search(str(event.get("source_ref", "")))
    return match.group(1) if match else str(event["id"])


def test_file_parses_and_has_enough_events(events: list[dict[str, object]]) -> None:
    assert len(events) >= _MIN_EVENTS


def test_ids_unique(events: list[dict[str, object]]) -> None:
    ids = [e["id"] for e in events]
    assert len(ids) == len(set(ids))


def test_methods_valid(events: list[dict[str, object]]) -> None:
    assert all(e["method"] in _METHODS for e in events)


def test_bboxes_construct_valid(events: list[dict[str, object]]) -> None:
    for e in events:
        west, south, east, north = e["bbox"]  # type: ignore[misc]
        bbox = BBox(west=west, south=south, east=east, north=north)
        # source must fall inside the analysis bbox
        lon, lat = e["lon"], e["lat"]
        assert bbox.west <= lon <= bbox.east  # type: ignore[operator]
        assert bbox.south <= lat <= bbox.north  # type: ignore[operator]


def test_mbsp_rows_have_source_lonlat(events: list[dict[str, object]]) -> None:
    for e in events:
        if e["method"] == "mbsp":
            assert e["source_lonlat"] is not None
            assert len(e["source_lonlat"]) == 2  # type: ignore[arg-type]


def test_published_values_present_and_positive(events: list[dict[str, object]]) -> None:
    for e in events:
        assert isinstance(e["published_q_t_h"], (int, float))
        assert e["published_q_t_h"] > 0  # type: ignore[operator]
        assert isinstance(e["published_sigma_t_h"], (int, float))
        assert e["published_sigma_t_h"] > 0  # type: ignore[operator]
        assert str(e["published_instrument"]).startswith("Sentinel-2")
        assert e["source_ref"]  # non-empty provenance string


def test_per_site_cap(events: list[dict[str, object]]) -> None:
    counts: dict[str, int] = {}
    for e in events:
        key = _site_key(e)
        counts[key] = counts.get(key, 0) + 1
    worst = max(counts.values())
    assert worst <= _MAX_PER_SITE, f"a site has {worst} events (cap {_MAX_PER_SITE}): {counts}"


def test_region_diversity(events: list[dict[str, object]]) -> None:
    regions = {str(e["region"]) for e in events}
    assert len(regions) >= _MIN_REGIONS


def test_rate_span(events: list[dict[str, object]]) -> None:
    rates = [float(e["published_q_t_h"]) for e in events]  # type: ignore[arg-type]
    # published rates should span roughly 5–30 t/h (a model, not a site, calibration)
    assert min(rates) <= 6.0
    assert max(rates) >= 20.0


def test_reference_pinned_for_mbmp_when_degenerate(events: list[dict[str, object]]) -> None:
    # MBMP events either pin a reference id or explicitly allow auto (None) — but any
    # event whose notes flag an orbit-degenerate auto pick MUST pin the reference.
    for e in events:
        if "degenerate" in str(e.get("notes", "")):
            assert e["reference_scene_id"] is not None


# ── Stage 1b: aggregate diagnostics (hand-checked on 4 synthetic events) ──

# Chosen so slope, median ratio, and Theil–Sen all resolve to exactly 1.0:
#   q_pub  = [10, 10, 20, 20]
#   q_ours = [11,  9, 24, 16]  (ratios 1.1, 0.9, 1.2, 0.8)
_SYNTH_PUB = np.array([10.0, 10.0, 20.0, 20.0])
_SYNTH_OURS = np.array([11.0, 9.0, 24.0, 16.0])


def test_slope_through_origin() -> None:
    h = _load_harness()
    # Σ(qo·qp)/Σ(qp²) = (110+90+480+320)/(100+100+400+400) = 1000/1000 = 1.0
    assert h.slope_through_origin(_SYNTH_OURS, _SYNTH_PUB) == pytest.approx(1.0)
    # degenerate: all published zero → NaN, not a crash
    assert np.isnan(h.slope_through_origin(_SYNTH_OURS, np.zeros(4)))


def test_median_ratio() -> None:
    h = _load_harness()
    # ratios sorted [0.8, 0.9, 1.1, 1.2] → median = (0.9 + 1.1)/2 = 1.0
    assert h.median_ratio(_SYNTH_OURS, _SYNTH_PUB) == pytest.approx(1.0)


def test_theil_sen_slope() -> None:
    h = _load_harness()
    # pairwise slopes over dx≠0 pairs: 1.3, 0.5, 1.5, 0.7 → median = (0.7+1.3)/2 = 1.0
    assert h.theil_sen_slope(_SYNTH_OURS, _SYNTH_PUB) == pytest.approx(1.0)


def test_log_scatter_matches_definition() -> None:
    h = _load_harness()
    log_ratio = np.log10(_SYNTH_OURS / _SYNTH_PUB)
    expected = 1.4826 * np.median(np.abs(log_ratio - np.median(log_ratio)))
    assert h.log_scatter(_SYNTH_OURS, _SYNTH_PUB) == pytest.approx(expected)
    # identical retrievals → zero scatter
    assert h.log_scatter(_SYNTH_PUB, _SYNTH_PUB) == pytest.approx(0.0)


# ── Stage 1b: frozen baseline (version-coupled to the packaged LUT) ──


def _current_baseline_path() -> Path:
    version = conversion.load_lut().version
    return _REPO_ROOT / "scripts" / "data" / f"calibration_baseline_v{version}.json"


def test_baseline_couples_to_packaged_lut_version() -> None:
    # A baseline MUST exist for whatever LUT the package currently ships, and its stamped
    # lut_version must match. This forcibly fails the suite whenever the LUT bumps until the
    # baseline is regenerated — the mechanism that keeps the Stage 3 LUT swap honest.
    path = _current_baseline_path()
    version = conversion.load_lut().version
    assert path.exists(), f"no calibration baseline for packaged LUT v{version} — regenerate it"
    with open(path) as fh:
        baseline = json.load(fh)
    assert baseline["lut_version"] == version


def test_committed_baseline_schema_and_consistency() -> None:
    path = _current_baseline_path()
    if not path.exists():  # covered by the coupling test above; skip schema check if absent
        pytest.skip("baseline for the packaged LUT not present")
    with open(path) as fh:
        baseline = json.load(fh)
    assert baseline["mc_seed"] == 0
    quantified = [r for r in baseline["events"] if not r["excluded"]]
    assert len(quantified) >= _MIN_EVENTS
    assert baseline["aggregates"]["n_quantified"] == len(quantified)
    for r in quantified:
        assert r["q_ours_t_h"] is not None
        assert r["q_ours_t_h"] > 0
    for r in baseline["events"]:
        if r["excluded"]:
            assert r["exclusion_reason"]
