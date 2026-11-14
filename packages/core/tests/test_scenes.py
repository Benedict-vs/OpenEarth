"""Stage 2 — scene search + reference auto-selection (offline)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pytest

from openearth.errors import RetrievalError
from openearth.geometry import BBox
from openearth.methane import scenes as scenes_mod
from openearth.methane.scenes import (
    S2Scene,
    dedupe_overpasses,
    list_scenes,
    pick_reference,
    pick_reference_set,
)


def _scene(
    scene_id: str,
    day: int,
    *,
    cloud: float = 5.0,
    orbit: int = 50,
    sat: str = "Sentinel-2A",
) -> S2Scene:
    return S2Scene(
        scene_id=scene_id,
        time=datetime(2018, 6, day, 7, 30, tzinfo=UTC),
        cloud_pct=cloud,
        relative_orbit=orbit,
        spacecraft=sat,
        sun_zenith_deg=30.0,
        view_zenith_deg=5.0,
    )


# ── S2Scene.amf ──


def test_amf_cardinal() -> None:
    s = S2Scene("x", datetime(2018, 6, 1, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 40.0, 0.0)
    assert s.amf == pytest.approx(1.0 / math.cos(math.radians(40)) + 1.0)
    assert s.amf == pytest.approx(2.305, abs=1e-3)


# ── pick_reference ──


def test_pick_reference_prefers_same_orbit_within_penalty() -> None:
    target = _scene("t", 19, orbit=50)
    # Nearer in time but different orbit (+30) vs a bit further but same orbit.
    near_diff_orbit = _scene("a", 17, orbit=93)  # |Δt|=2, +30 -> 32
    far_same_orbit = _scene("b", 9, orbit=50)  # |Δt|=10, +0 -> 10
    assert pick_reference(target, [near_diff_orbit, far_same_orbit]).scene_id == "b"


def test_pick_reference_switches_when_penalty_exceeded() -> None:
    target = _scene("t", 19, orbit=50)
    # Different orbit but very close vs same orbit but > 30 days away.
    near_diff_orbit = _scene("a", 18, orbit=93)  # 1 + 30 = 31
    far_same_orbit = _scene("b", 1, orbit=50)  # 18 -> 18 wins
    assert pick_reference(target, [near_diff_orbit, far_same_orbit]).scene_id == "b"
    # Now push the same-orbit one out to > 31 days total penalty.
    far_same_orbit2 = S2Scene(
        "c", datetime(2018, 5, 1, 7, 30, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0
    )  # ~49 days
    assert pick_reference(target, [near_diff_orbit, far_same_orbit2]).scene_id == "a"


def test_pick_reference_spacecraft_penalty_breaks_ties() -> None:
    target = _scene("t", 19, orbit=50, sat="Sentinel-2A")
    same_sat = _scene("a", 12, orbit=50, sat="Sentinel-2A")  # 7
    diff_sat = _scene("b", 12, orbit=50, sat="Sentinel-2B")  # 7 + 5
    assert pick_reference(target, [same_sat, diff_sat]).scene_id == "a"


def test_pick_reference_excludes_target() -> None:
    target = _scene("t", 19)
    assert pick_reference(target, [target]) is None


def test_pick_reference_cloud_gate() -> None:
    target = _scene("t", 19)
    cloudy = _scene("a", 12, cloud=80.0)
    assert pick_reference(target, [cloudy], max_cloud=30.0) is None


def test_pick_reference_max_days_gate() -> None:
    target = _scene("t", 19)
    far = S2Scene("a", datetime(2018, 1, 1, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0)
    assert pick_reference(target, [far], max_days=120) is None


def test_pick_reference_none_when_empty() -> None:
    assert pick_reference(_scene("t", 19), []) is None


def test_pick_reference_excludes_same_overpass() -> None:
    # A same-day adjacent-tile scene (Δt ≈ 0) images the same plume; it must be
    # excluded so a real, plume-free different-date reference is chosen instead.
    target = _scene("t", 19)
    same_overpass = S2Scene(
        "sameday", datetime(2018, 6, 19, 7, 40, tzinfo=UTC), 5.0, 50, "Sentinel-2B", 30.0, 5.0
    )
    real_ref = _scene("earlier", 14)  # 5 days before
    assert pick_reference(target, [same_overpass, real_ref]).scene_id == "earlier"


# ── pick_reference_set (composite reference) ──


def test_pick_reference_set_nearest_k_same_orbit_spacecraft() -> None:
    target = _scene("t", 19, orbit=50, sat="Sentinel-2A")
    cands = [
        _scene("d3", 16),  # |Δt| 3
        _scene("d1", 18),  # |Δt| 1 (nearest)
        _scene("d5", 14),  # |Δt| 5
        _scene("d9", 10),  # |Δt| 9
    ]
    picked = pick_reference_set(target, cands, 3)
    assert [s.scene_id for s in picked] == ["d1", "d3", "d5"]  # nearest 3, in order


def test_pick_reference_set_hard_filters_orbit_and_spacecraft() -> None:
    target = _scene("t", 19, orbit=50, sat="Sentinel-2A")
    good = _scene("ok", 17, orbit=50, sat="Sentinel-2A")
    diff_orbit = _scene("orb", 18, orbit=93, sat="Sentinel-2A")  # excluded (hard)
    diff_sat = _scene("sat", 18, orbit=50, sat="Sentinel-2B")  # excluded (hard)
    picked = pick_reference_set(target, [good, diff_orbit, diff_sat], 5)
    assert [s.scene_id for s in picked] == ["ok"]


def test_pick_reference_set_cloud_overpass_and_range_gates() -> None:
    target = _scene("t", 19)
    cloudy = _scene("cloud", 15, cloud=80.0)  # excluded
    same_overpass = S2Scene(
        "same", datetime(2018, 6, 19, 7, 40, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0
    )  # Δt ≈ 0 → excluded
    far = S2Scene("far", datetime(2018, 1, 1, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0)
    good = _scene("good", 12)
    picked = pick_reference_set(target, [cloudy, same_overpass, far, good], 5)
    assert [s.scene_id for s in picked] == ["good"]


def test_pick_reference_set_empty_when_nothing_qualifies() -> None:
    target = _scene("t", 19, orbit=50)
    assert pick_reference_set(target, [_scene("x", 18, orbit=93)], 5) == []


# ── tile copies of one acquisition (UTM-zone overlap) ──


def _tiled(
    datatake_day: int, tile: str, *, second: int = 0, cloud: float = 0.0, sat: str = "Sentinel-2B"
) -> S2Scene:
    # Real Korpezhe layout: one datatake listed on T39SYC and T40SBH, the
    # T40SBH copy timestamped ~0.8 s earlier.
    return S2Scene(
        scene_id=f"201806{datatake_day:02d}T070619_201806{datatake_day:02d}T071300_T{tile}",
        time=datetime(2018, 6, datatake_day, 7, 19, second, tzinfo=UTC),
        cloud_pct=cloud,
        relative_orbit=106,
        spacecraft=sat,
        sun_zenith_deg=20.0,
        view_zenith_deg=5.0,
    )


def test_dedupe_overpasses_prefers_target_tile_then_cloud() -> None:
    a_39, a_40 = _tiled(29, "39SYC", second=1), _tiled(29, "40SBH", second=0)
    b_40_clear, b_40_cloudy = _tiled(9, "40SBH", cloud=1.0), _tiled(9, "40SBH", cloud=9.0)
    b_40_cloudy = S2Scene(  # a reprocessed duplicate: same datatake, other generation id
        "20180609T070619_20180609T090000_T40SBH",
        b_40_cloudy.time,
        9.0,
        106,
        "Sentinel-2B",
        20.0,
        5.0,
    )
    kept = dedupe_overpasses([a_40, a_39, b_40_cloudy, b_40_clear], prefer_tile="39SYC")
    assert [s.scene_id for s in kept] == [a_39.scene_id, b_40_clear.scene_id]


def test_dedupe_overpasses_keeps_distinct_spacecraft() -> None:
    s2a, s2b = _tiled(24, "39SYC", sat="Sentinel-2A"), _tiled(24, "39SYC", sat="Sentinel-2B")
    assert len(dedupe_overpasses([s2a, s2b], prefer_tile="39SYC")) == 2


def test_pick_reference_prefers_same_tile_copy() -> None:
    # Before the fix the T40SBH copy won the sub-second Δt tie → cross-tile reference.
    target = _tiled(19, "39SYC", second=0)
    copies = [_tiled(29, "40SBH", second=0), _tiled(29, "39SYC", second=1)]
    assert pick_reference(target, copies).scene_id.endswith("_T39SYC")


def test_pick_reference_set_counts_each_overpass_once() -> None:
    # Korpezhe 2018-06-19: 06-29/07-09 were each listed twice, so "5 members"
    # were 3 overpasses. Now k members are k distinct acquisitions.
    target = _tiled(19, "39SYC")
    cands = [_tiled(d, t) for d in (24, 29) for t in ("39SYC", "40SBH")]
    picked = pick_reference_set(target, cands, 5)
    assert [s.scene_id[:8] for s in picked] == ["20180624", "20180629"]
    assert all(s.scene_id.endswith("_T39SYC") for s in picked)


# ── list_scenes parsing (canned getInfo payload) ──


def _feature(
    scene_id: str,
    time_ms: int,
    cloud: float,
    orbit: int,
    sat: str,
    sun: float,
    view: float | None,
) -> dict[str, Any]:
    return {
        "properties": {
            "scene_id": scene_id,
            "time": time_ms,
            "cloud_pct": cloud,
            "relative_orbit": orbit,
            "spacecraft": sat,
            "sun_zenith": sun,
            "view_zenith": view,
        }
    }


def _fake_features(*features: dict[str, Any]) -> Any:
    return lambda *a, **k: list(features)


def test_list_scenes_parses_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    # The canned getInfo features arrive out of order; list_scenes sorts by time.
    monkeypatch.setattr(
        scenes_mod,
        "_fetch_scene_features",
        _fake_features(
            _feature("later", 1_529_500_000_000, 10.0, 50, "Sentinel-2A", 40.0, 0.0),
            _feature("earlier", 1_528_000_000_000, 5.0, 93, "Sentinel-2B", 35.0, 6.0),
        ),
    )
    result = list_scenes(BBox(53.7, 38.2, 54.7, 38.8), "2018-06-01", "2018-07-01")
    assert [s.scene_id for s in result] == ["earlier", "later"]
    assert result[0].spacecraft == "Sentinel-2B"
    assert result[1].amf == pytest.approx(2.305, abs=1e-3)


def test_list_scenes_missing_zenith_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scenes_mod,
        "_fetch_scene_features",
        _fake_features(_feature("bad", 1_528_000_000_000, 5.0, 50, "Sentinel-2A", 40.0, None)),
    )
    with pytest.raises(RetrievalError, match="view_zenith"):
        list_scenes(BBox(53.7, 38.2, 54.7, 38.8), "2018-06-01", "2018-07-01")
