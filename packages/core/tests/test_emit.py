"""EMIT plume model — V002 GeoJSON parser, cross-match, dedup, date router (offline)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openearth.methane.emit import (
    GEE_CH4PLM_CUTOFF,
    EmitPlume,
    _plumes_from_gee_features,  # tested directly: the pure GEE half
    cross_match,
    dedup_plumes,
    gee_available,
    parse_v002_geojson,
)

# A real, trimmed LP DAAC V002 CH4PLM granule (public-domain NASA data).
_FIXTURE = Path(__file__).parent / "data" / "emit_v002_plm_granule.geojson"

# A synthetic V002 feature carrying an emission rate + portal Scene FIDs — the
# real committed granule has an NA rate (no concurrent wind), so these paths are
# exercised inline (schema-faithful per the addendum's verified keys).
_V002_WITH_RATE = json.dumps(
    {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "Plume ID": "CH4_PlumeComplex-2015",
                    "Scene FIDs": ["emit20240402t201133", "emit20240402t201145"],
                    "UTC Time Observed": "2024-04-02T20:11:33Z",
                    "Max Plume Concentration (ppm m)": 1187.0,
                    "Latitude of max concentration": 32.4501,
                    "Longitude of max concentration": -101.8203,
                    "Emissions Rate Estimate (kg/hr)": 1620.4,
                    "Emissions Rate Estimate Uncertainty (kg/hr)": 540.2,
                    "Wind Speed (m/s)": "NA",
                    "style": {"color": "#ff0000"},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-101.822, 32.448],
                            [-101.818, 32.448],
                            [-101.818, 32.452],
                            [-101.822, 32.452],
                            [-101.822, 32.448],
                        ]
                    ],
                },
            },
            {  # portal aggregate pairs each plume with a max-enhancement Point — skip it
                "type": "Feature",
                "properties": {"Plume ID": "CH4_PlumeComplex-2015"},
                "geometry": {"type": "Point", "coordinates": [-101.8203, 32.4501]},
            },
        ],
    }
).encode()


def _polygon(lon: float, lat: float) -> dict:
    d = 0.002
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - d, lat - d],
                [lon + d, lat - d],
                [lon + d, lat + d],
                [lon - d, lat + d],
                [lon - d, lat - d],
            ]
        ],
    }


def _plume(
    *,
    plume_id: str = "p",
    lat: float | None = 32.0,
    lon: float | None = -102.0,
    time: str = "2024-03-15T18:05:12Z",
    provenance: str = "lpdaac_v002",
    q: float | None = None,
) -> EmitPlume:
    return EmitPlume(
        plume_id=plume_id,
        outline=_polygon(lon if lon is not None else -102.0, lat if lat is not None else 32.0),
        time_utc=datetime.fromisoformat(time.replace("Z", "+00:00")),
        max_enh_ppm_m=None,
        max_enh_lat=lat,
        max_enh_lon=lon,
        q_kg_h=q,
        q_sigma_kg_h=None,
        provenance=provenance,
        source_scenes=[],
    )


# ── parse_v002_geojson ──


def test_parse_v002_real_daac_granule() -> None:
    # The committed fixture is a real trimmed LP DAAC V002 granule (plume 3374,
    # 2025-09-22 Permian). Its emission rate is genuinely "NA" (no wind) → None.
    plumes = parse_v002_geojson(_FIXTURE.read_bytes())
    assert len(plumes) == 1
    p = plumes[0]
    assert p.plume_id == "CH4_PlumeComplex-3374"
    assert p.provenance == "lpdaac_v002"
    assert p.time_utc == datetime(2025, 9, 22, 20, 49, 33, tzinfo=UTC)
    assert p.max_enh_ppm_m == pytest.approx(4699.0)
    assert p.max_enh_lat == pytest.approx(32.24272)
    assert p.max_enh_lon == pytest.approx(-102.04762)
    # Real granule: "NA" rate/wind coerce to None (not 0.0, not the literal string).
    assert p.q_kg_h is None
    assert p.q_sigma_kg_h is None
    # DAAC Scene Names (a single-element list) become the source scenes.
    assert p.source_scenes == ["EMIT_L2B_CH4ENH_V002_20250922T204933_2526514_006"]
    assert p.outline["type"] == "Polygon"


def test_parse_v002_emission_rate_and_fid_fallback() -> None:
    plumes = parse_v002_geojson(_V002_WITH_RATE)
    # Two features but the trailing max-enhancement Point is skipped.
    assert len(plumes) == 1
    p = plumes[0]
    assert p.plume_id == "CH4_PlumeComplex-2015"
    assert p.max_enh_ppm_m == pytest.approx(1187.0)
    # V002 emission rate + uncertainty parsed as plain floats.
    assert p.q_kg_h == pytest.approx(1620.4)
    assert p.q_sigma_kg_h == pytest.approx(540.2)
    # No DAAC Scene Names → falls back to the portal Scene FIDs (a 2-element list).
    assert p.source_scenes == ["emit20240402t201133", "emit20240402t201145"]


def test_parse_v002_accepts_bare_feature() -> None:
    doc = json.loads(_FIXTURE.read_text())
    bare = json.dumps(doc["features"][0]).encode()
    plumes = parse_v002_geojson(bare)
    assert len(plumes) == 1
    assert plumes[0].plume_id == "CH4_PlumeComplex-3374"


def test_parse_v002_skips_malformed_and_empty() -> None:
    # Missing Plume ID / unparseable time / no geometry → skipped, not fatal.
    bad = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": _polygon(-102.0, 32.0)},
                {"type": "Feature", "properties": {"Plume ID": "x"}, "geometry": None},
            ],
        }
    ).encode()
    assert parse_v002_geojson(bad) == []


# ── cross_match ──


def test_cross_match_within_windows() -> None:
    plume = _plume(lat=31.9042, lon=-102.1187, time="2024-03-15T18:05:12Z")
    # Same spot, same day → matches.
    matches = cross_match(31.9042, -102.1187, datetime(2024, 3, 15, 18, 0, tzinfo=UTC), [plume])
    assert len(matches) == 1
    assert matches[0].distance_km == pytest.approx(0.0, abs=0.05)
    assert matches[0].dt_days == pytest.approx(5 / (60 * 24), abs=1e-3)


def test_cross_match_distance_and_time_cutoffs() -> None:
    plume = _plume(lat=32.0, lon=-102.0, time="2024-03-15T18:00:00Z")
    det_time = datetime(2024, 3, 15, 18, 0, tzinfo=UTC)
    # ~11 km east (Δlon 0.12° at 32°N ≈ 11 km) → outside default max_km=5.
    assert cross_match(32.0, -101.88, det_time, [plume]) == []
    # Same location, but 4 days off → outside default max_days=3.
    late = datetime(2024, 3, 19, 18, 0, tzinfo=UTC)
    assert cross_match(32.0, -102.0, late, [plume]) == []
    # Widening the windows recovers the match.
    assert len(cross_match(32.0, -101.88, det_time, [plume], max_km=20.0)) == 1


def test_cross_match_sorted_nearest_first() -> None:
    near = _plume(plume_id="near", lat=32.001, lon=-102.0)
    far = _plume(plume_id="far", lat=32.03, lon=-102.0)
    matches = cross_match(32.0, -102.0, datetime(2024, 3, 15, 18, 5, 12, tzinfo=UTC), [far, near])
    assert [m.plume.plume_id for m in matches] == ["near", "far"]
    assert matches[0].distance_km < matches[1].distance_km


def test_cross_match_naive_detection_time_assumed_utc() -> None:
    plume = _plume(lat=32.0, lon=-102.0, time="2024-03-15T18:00:00Z")
    naive = datetime(2024, 3, 15, 18, 0)  # deliberately naive
    assert len(cross_match(32.0, -102.0, naive, [plume])) == 1


def test_cross_match_uses_centroid_when_max_enh_absent() -> None:
    # A V001-style plume (no max-enh coords) matches via its outline centroid.
    plume = EmitPlume(
        plume_id="v1",
        outline=_polygon(-102.0, 32.0),
        time_utc=datetime(2024, 3, 15, 18, 0, tzinfo=UTC),
        max_enh_ppm_m=1500.0,
        max_enh_lat=None,
        max_enh_lon=None,
        q_kg_h=None,
        q_sigma_kg_h=None,
        provenance="gee_v001",
        source_scenes=[],
    )
    # Mean-of-vertices centroid — a representative point, not a precise centroid.
    assert plume.representative_point() == pytest.approx((32.0, -102.0), abs=0.01)
    assert len(cross_match(32.0, -102.0, datetime(2024, 3, 15, 18, 0, tzinfo=UTC), [plume])) == 1


# ── dedup_plumes ──


def test_dedup_collapses_v001_v002_duplicate_keeping_v002() -> None:
    v1 = _plume(plume_id="idx_1", lat=32.0, lon=-102.0, provenance="gee_v001")
    v2 = _plume(plume_id="CH4-99", lat=32.0005, lon=-102.0005, provenance="lpdaac_v002", q=800.0)
    kept = dedup_plumes([v1, v2])
    assert len(kept) == 1
    assert kept[0].provenance == "lpdaac_v002"  # V002 wins (carries the emission rate)
    assert kept[0].q_kg_h == pytest.approx(800.0)


def test_dedup_keeps_distinct_plumes() -> None:
    a = _plume(plume_id="a", lat=32.0, lon=-102.0, time="2024-03-15T18:00:00Z")
    b = _plume(plume_id="b", lat=33.0, lon=-101.0, time="2024-03-15T18:00:00Z")  # far away
    # same spot, different day → distinct
    c = _plume(plume_id="c", lat=32.0, lon=-102.0, time="2024-04-01T18:00:00Z")
    assert len(dedup_plumes([a, b, c])) == 3


# ── gee_available (date router) ──


def test_gee_available_boundary() -> None:
    assert GEE_CH4PLM_CUTOFF.isoformat() == "2024-10-26"
    # Entirely before the freeze → GEE covers it.
    assert gee_available("2023-06-01", "2023-07-01") is True
    # Window ending exactly on the last GEE granule date is still covered.
    assert gee_available("2024-10-01", "2024-10-26") is True
    # Ending after the freeze → not fully covered (V002 needed).
    assert gee_available("2024-10-01", "2024-11-15") is False
    # Entirely after → not covered.
    assert gee_available("2025-01-01", "2025-02-01") is False


# ── _plumes_from_gee_features (pure GEE half) ──


def test_gee_features_to_plumes() -> None:
    # system:time_start is epoch-ms; 1710525912000 == 2024-03-15T18:05:12Z.
    features = [
        {
            "geometry": _polygon(-102.1, 31.9),
            "properties": {
                "plume_id": "20240315t180512",
                "time_start": 1710525912000,
                "max_enh": 3100.0,
            },
        },
        {  # a stray point / non-polygon result is ignored
            "geometry": {"type": "Point", "coordinates": [-102.0, 32.0]},
            "properties": {"plume_id": "z", "time_start": 1710525912000},
        },
    ]
    plumes = _plumes_from_gee_features(features)
    assert len(plumes) == 1
    p = plumes[0]
    assert p.provenance == "gee_v001"
    assert p.plume_id == "20240315t180512"
    assert p.time_utc == datetime(2024, 3, 15, 18, 5, 12, tzinfo=UTC)
    assert p.max_enh_ppm_m == pytest.approx(3100.0)
    # V001 carries no emission rate and no max-enh point → centroid representative.
    assert p.q_kg_h is None
    assert p.max_enh_lat is None
    assert p.representative_point() == pytest.approx((31.9, -102.1), abs=0.01)
