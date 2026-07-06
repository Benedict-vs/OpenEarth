"""Stage 1a — calibration event set validation (offline, zero Earth Engine).

Guards the committed ``scripts/data/calibration_events.json`` against the
curation criteria in docs/phase3.5-execution-plan.md: same-scene S2 events with
provenance, N ≥ 10, per-site cap, region diversity, valid bboxes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openearth.geometry import BBox

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVENTS_PATH = _REPO_ROOT / "scripts" / "data" / "calibration_events.json"

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
