"""Noise-floor loader + resolution (fix 1 + fix 9b), offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openearth_api.services.noise_floor import load_floor, resolve_floor

_FLOOR = {
    "version": 1,
    "sites": {
        "Korpezhe, Turkmenistan": {"floor_kg_h": 6300.0, "detect_rate": 0.8, "n_pairs": 5},
        "Basra oil fields, Iraq": {"floor_kg_h": None, "detect_rate": 0.0, "n_pairs": 5},
    },
    "global": {"floor_kg_h": 5000.0, "n_detected": 20},
}


def test_resolve_floor_prefers_site() -> None:
    floor, source, below = resolve_floor(_FLOOR, "Korpezhe, Turkmenistan", 4000.0)
    assert floor == 6300.0
    assert source == "site"
    assert below is True  # 4000 ≤ 6300


def test_resolve_floor_above_site_floor_not_below() -> None:
    _, _, below = resolve_floor(_FLOOR, "Korpezhe, Turkmenistan", 9000.0)
    assert below is False  # 9000 > 6300


def test_resolve_floor_unknown_site_uses_global() -> None:
    floor, source, below = resolve_floor(_FLOOR, "Some Custom Site", 4000.0)
    assert floor == 5000.0
    assert source == "global"
    assert below is True


def test_resolve_floor_none_site_uses_global() -> None:
    _, source, _ = resolve_floor(_FLOOR, None, 6000.0)
    assert source == "global"


def test_resolve_floor_site_with_null_floor_falls_back_to_global() -> None:
    # Basra detected nothing (floor_kg_h None) → the pooled global floor.
    floor, source, _ = resolve_floor(_FLOOR, "Basra oil fields, Iraq", 100.0)
    assert floor == 5000.0
    assert source == "global"


def test_resolve_floor_no_data_is_empty_context() -> None:
    floor, source, below = resolve_floor({}, "Korpezhe, Turkmenistan", 4000.0)
    assert floor is None
    assert source is None
    assert below is False


def test_resolve_floor_needs_q_to_flag_below() -> None:
    _, _, below = resolve_floor(_FLOOR, "Korpezhe, Turkmenistan", None)
    assert below is False


def test_load_floor_reads_file_and_handles_absence(tmp_path: Path) -> None:
    path = tmp_path / "noise_floor_v1.json"
    path.write_text(json.dumps(_FLOOR))
    assert load_floor(path)["version"] == 1
    # A missing floor (not frozen yet) loads as empty context, never an error.
    assert load_floor(tmp_path / "missing.json") == {}


def test_packaged_floor_schema_when_present() -> None:
    """If the v1 floor has been frozen, validate its shape (schema pin)."""
    floor = load_floor()  # packaged
    if not floor:
        return  # not frozen yet — covered by the loader tests above
    assert floor["version"] == 1
    assert set(floor["provenance"]) >= {"git_hash", "lut_version", "run_utc", "window"}
    assert "floor_kg_h" in floor["global"]
    for entry in floor["sites"].values():
        assert {"n_pairs", "detect_rate", "q_noise_kg_h", "floor_kg_h"} <= set(entry)


def test_site_noise_floor_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openearth_api.services import methane as svc

    sites = client.get("/api/methane/sites").json()
    name, sid = sites[0]["name"], sites[0]["id"]
    monkeypatch.setattr(
        svc,
        "load_floor",
        lambda: {
            "sites": {name: {"floor_kg_h": 6300.0, "detect_rate": 0.8, "n_pairs": 5}},
            "global": {"floor_kg_h": 5000.0},
        },
    )
    body = client.get(f"/api/methane/sites/{sid}/noise-floor").json()
    assert body["floor_kg_h"] == 6300.0
    assert body["floor_source"] == "site"
    assert body["detect_rate"] == 0.8
    assert body["n_pairs"] == 5


def test_site_noise_floor_route_global_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openearth_api.services import methane as svc

    sid = client.get("/api/methane/sites").json()[0]["id"]
    monkeypatch.setattr(svc, "load_floor", lambda: {"sites": {}, "global": {"floor_kg_h": 5000.0}})
    body = client.get(f"/api/methane/sites/{sid}/noise-floor").json()
    assert body["floor_kg_h"] == 5000.0
    assert body["floor_source"] == "global"
    assert body["n_pairs"] is None
