"""Stage 9 — reference-event parsing + detection cross-match (offline)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from openearth.methane.validation import (
    ReferenceEvent,
    haversine_km,
    match_detection,
    parse_events,
)

# ── parse_events ──


def test_parse_csv_header_tolerance_and_t_h_conversion() -> None:
    csv = (
        b"latitude,longitude,detection_date,source_rate_t_h,uncertainty\n"
        b"38.5,53.9,2018-06-19,11.2,5.2\n"
        b"31.6,5.9,2019-11-20T09:00:00Z,9.3,\n"
    )
    events = parse_events(csv, fmt="csv", source="imeo")
    assert len(events) == 2
    assert events[0].lat == 38.5
    assert events[0].q_kg_h == pytest.approx(11200.0)  # 11.2 t/h → kg/h
    assert events[0].q_sigma_kg_h == pytest.approx(5200.0)
    assert events[1].q_sigma_kg_h is None
    assert events[0].event_time_utc.startswith("2018-06-19")


def test_parse_csv_skips_unparseable_rows() -> None:
    csv = (
        b"lat,lon,date,rate\n"
        b"38.5,53.9,2018-06-19,10\n"  # good
        b",53.9,2018-06-19,10\n"  # missing lat
        b"38.5,53.9,not-a-date,10\n"  # bad date
    )
    events = parse_events(csv, fmt="csv", source="manual")
    assert len(events) == 1  # two rows skipped


def test_parse_geojson_points() -> None:
    doc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [53.9, 38.5]},
                "properties": {"date": "2018-06-19", "source_rate_t_h": 11.2},
            },
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []}},
        ],
    }
    events = parse_events(json.dumps(doc).encode(), fmt="geojson", source="sron")
    assert len(events) == 1
    assert events[0].lon == 53.9
    assert events[0].lat == 38.5
    assert events[0].q_kg_h == pytest.approx(11200.0)  # t/h column → ×1000
    assert events[0].rate_unit == "t_h"


# ── fix 13: unit-safe rate import (per-column unit provenance) ──


def test_imeo_kg_h_column_not_scaled() -> None:
    """UNEP-IMEO MARS `ch4_fluxrate` is already kg/h — no ×1000 (Tier 3 P3)."""
    csv = b"lat,lon,tile_date,ch4_fluxrate,ch4_fluxrate_std\n38.5,53.9,2019-11-20,1620.4,540.2\n"
    events = parse_events(csv, fmt="csv", source="imeo")
    assert len(events) == 1
    assert events[0].q_kg_h == pytest.approx(1620.4)  # kg/h passes through
    assert events[0].q_sigma_kg_h == pytest.approx(540.2)
    assert events[0].rate_unit == "kg_h"
    assert "rate_dropped" not in events[0].raw


def test_sron_t_h_column_still_scaled() -> None:
    csv = b"lat,lon,date,source_rate_t_h\n38.5,53.9,2018-06-19,9.3\n"
    events = parse_events(csv, fmt="csv", source="sron")
    assert events[0].q_kg_h == pytest.approx(9300.0)  # t/h → kg/h
    assert events[0].rate_unit == "t_h"


def test_kg_h_suffix_column_self_describes() -> None:
    csv = b"lat,lon,date,flux_kg_h\n38.5,53.9,2018-06-19,1500\n"
    events = parse_events(csv, fmt="csv", source="x")
    assert events[0].q_kg_h == pytest.approx(1500.0)
    assert events[0].rate_unit == "kg_h"


def test_agnostic_rate_dropped_under_auto() -> None:
    """A unit-agnostic `rate` is never guessed: auto → None, event still imports."""
    csv = b"lat,lon,date,rate\n38.5,53.9,2018-06-19,11.2\n"
    events = parse_events(csv, fmt="csv", source="x")  # default unit="auto"
    assert len(events) == 1  # counted
    assert events[0].q_kg_h is None
    assert events[0].rate_unit is None
    assert events[0].raw["rate_dropped"] == "ambiguous_unit"


def test_agnostic_rate_scaled_with_explicit_unit() -> None:
    csv = b"lat,lon,date,rate\n38.5,53.9,2018-06-19,11.2\n"
    th = parse_events(csv, fmt="csv", source="x", unit="t_h")
    assert th[0].q_kg_h == pytest.approx(11200.0)
    assert th[0].rate_unit == "t_h"
    kg = parse_events(csv, fmt="csv", source="x", unit="kg_h")
    assert kg[0].q_kg_h == pytest.approx(11.2)
    assert kg[0].rate_unit == "kg_h"


def test_declared_column_beats_explicit_unit() -> None:
    """A unit-declared column wins even when the caller passes a different unit."""
    csv = b"lat,lon,date,source_rate_t_h\n38.5,53.9,2018-06-19,9.3\n"
    events = parse_events(csv, fmt="csv", source="x", unit="kg_h")
    assert events[0].q_kg_h == pytest.approx(9300.0)  # t/h column, not kg/h
    assert events[0].rate_unit == "t_h"


def test_absurd_rate_dropped_by_guard() -> None:
    """> 500 t/h equivalent → rate dropped to None (unit error), event kept."""
    csv = b"lat,lon,date,source_rate_t_h\n38.5,53.9,2018-06-19,999\n"
    events = parse_events(csv, fmt="csv", source="x")
    assert len(events) == 1
    assert events[0].q_kg_h is None
    assert events[0].rate_unit == "t_h"  # detected unit kept for diagnosis
    assert events[0].raw["rate_dropped"] == "over_500_th_guard"


def test_agnostic_sigma_inherits_rate_unit() -> None:
    """`uncertainty` is unit-agnostic — it follows the resolved rate's unit."""
    csv = b"lat,lon,date,source_rate_t_h,uncertainty\n38.5,53.9,2018-06-19,9.3,2.0\n"
    events = parse_events(csv, fmt="csv", source="x")
    assert events[0].q_sigma_kg_h == pytest.approx(2000.0)  # inherited t/h → ×1000


# ── haversine ──


def test_haversine_cardinal() -> None:
    # 1° of latitude ≈ 111.19 km.
    assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.19, abs=0.5)
    assert haversine_km(38.0, 53.0, 38.0, 53.0) == pytest.approx(0.0, abs=1e-9)


# ── match_detection verdict windows ──


def _event(lat: float, lon: float, date: str) -> ReferenceEvent:
    return ReferenceEvent(
        source="test", event_time_utc=date, lat=lat, lon=lon, q_kg_h=None, q_sigma_kg_h=None, raw={}
    )


_DET_LAT, _DET_LON = 38.50, 53.90
_DET_TIME = datetime(2018, 6, 19, tzinfo=UTC)


def test_verdict_confirmed_within_14_days() -> None:
    events = [_event(38.50, 53.90, "2018-06-06T00:00:00+00:00")]  # 13 days, ~0 km
    verdict, matched = match_detection(_DET_LAT, _DET_LON, _DET_TIME, events)
    assert verdict == "confirmed"
    assert matched == [0]


def test_verdict_plausible_within_60_days() -> None:
    events = [_event(38.50, 53.90, "2018-05-05T00:00:00+00:00")]  # 45 days
    verdict, matched = match_detection(_DET_LAT, _DET_LON, _DET_TIME, events)
    assert verdict == "plausible"
    assert matched == [0]


def test_verdict_unvalidated_when_time_too_far() -> None:
    events = [_event(38.50, 53.90, "2018-03-21T00:00:00+00:00")]  # ~90 days
    verdict, matched = match_detection(_DET_LAT, _DET_LON, _DET_TIME, events)
    assert verdict == "unvalidated"
    assert matched == []


def test_verdict_unvalidated_when_too_far_in_space() -> None:
    # ~0.3° east ≈ 26 km at this latitude — beyond the 15 km default.
    events = [_event(38.50, 54.20, "2018-06-19T00:00:00+00:00")]
    verdict, _ = match_detection(_DET_LAT, _DET_LON, _DET_TIME, events)
    assert verdict == "unvalidated"
