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
                "properties": {"date": "2018-06-19", "q": 11.2},
            },
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []}},
        ],
    }
    events = parse_events(json.dumps(doc).encode(), fmt="geojson", source="sron")
    assert len(events) == 1
    assert events[0].lon == 53.9
    assert events[0].lat == 38.5
    assert events[0].q_kg_h == pytest.approx(11200.0)


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
