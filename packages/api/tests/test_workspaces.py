"""Workspace CRUD + versioned-state validation. No EE (plain DB CRUD)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from openearth_api.schemas import WorkspaceState

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BBOX = {"kind": "bbox", "west": -103.0, "south": 31.5, "east": -102.0, "north": 32.5}


def _state(**overrides: Any) -> dict[str, Any]:
    """A v2 (window/period) workspace state, normalized to the exact shape the
    server round-trips (every WorkspaceDate field present, unset ones null) so
    round-trip equality holds."""
    base: dict[str, Any] = {
        "v": 2,
        "layers": [
            {
                "dataset": "s2",
                "product": "NDVI",
                "label": "Sentinel-2 · NDVI",
                "opacity": 0.8,
                "visible": True,
                "viz_overrides": None,
            }
        ],
        "roi": BBOX,
        "date": {
            "center": "2024-06-15",
            "period_start": "2024-01-01",
            "period_end": "2024-12-31",
            "half_window_days": 15,
            # v1 migration fields — unset in v2, present as null after normalization.
            "mode": None,
            "start": None,
            "end": None,
            "target_date": None,
        },
        "wind": False,
    }
    base.update(overrides)
    return base


# A committed v1 snapshot: the pre-Phase-8 shape. The server still accepts it
# (the client migrates it to window/period on load) — kept so the acceptance
# path stays exercised after v1 stops being written.
_V1_STATE: dict[str, Any] = {
    "v": 1,
    "layers": [],
    "roi": None,
    "date": {
        "mode": "range",
        "start": "2024-03-01",
        "end": "2024-09-01",
        "target_date": "2024-09-01",
        "half_window_days": 3,
    },
    "wind": False,
}


def test_create_get_list_round_trip(client: TestClient) -> None:
    assert client.get("/api/workspaces").json() == []

    created = client.post("/api/workspaces", json={"name": "Permian demo", "state": _state()})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Permian demo"
    assert body["state"] == _state()
    assert body["created_at"] == body["updated_at"]
    ws_id = body["id"]

    fetched = client.get(f"/api/workspaces/{ws_id}")
    assert fetched.status_code == 200
    assert fetched.json()["state"] == _state()

    listed = client.get("/api/workspaces").json()
    assert [w["name"] for w in listed] == ["Permian demo"]


def test_whole_globe_roi_round_trips(client: TestClient) -> None:
    created = client.post("/api/workspaces", json={"name": "Global", "state": _state(roi=None)})
    assert created.status_code == 201
    assert created.json()["state"]["roi"] is None


def test_duplicate_name_is_409(client: TestClient) -> None:
    assert (
        client.post("/api/workspaces", json={"name": "Dup", "state": _state()}).status_code == 201
    )
    clash = client.post("/api/workspaces", json={"name": "Dup", "state": _state()})
    assert clash.status_code == 409
    assert "already exists" in clash.json()["detail"]


def test_update_replaces_state_and_bumps_updated_at(client: TestClient) -> None:
    created = client.post("/api/workspaces", json={"name": "W", "state": _state()}).json()
    ws_id, created_at = created["id"], created["created_at"]

    updated = client.put(
        f"/api/workspaces/{ws_id}",
        json={"name": "W renamed", "state": _state(wind=True)},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "W renamed"
    assert body["state"]["wind"] is True
    assert body["created_at"] == created_at  # creation stamp preserved
    assert body["updated_at"] >= created_at


def test_update_unknown_is_404(client: TestClient) -> None:
    assert (
        client.put("/api/workspaces/9999", json={"name": "X", "state": _state()}).status_code == 404
    )


def test_update_rename_onto_existing_is_409(client: TestClient) -> None:
    client.post("/api/workspaces", json={"name": "A", "state": _state()})
    b_id = client.post("/api/workspaces", json={"name": "B", "state": _state()}).json()["id"]
    clash = client.put(f"/api/workspaces/{b_id}", json={"name": "A", "state": _state()})
    assert clash.status_code == 409


def test_delete_then_get_is_404(client: TestClient) -> None:
    ws_id = client.post("/api/workspaces", json={"name": "Temp", "state": _state()}).json()["id"]
    assert client.delete(f"/api/workspaces/{ws_id}").status_code == 204
    assert client.get(f"/api/workspaces/{ws_id}").status_code == 404


def test_delete_unknown_is_404(client: TestClient) -> None:
    assert client.delete("/api/workspaces/9999").status_code == 404


def test_v1_state_still_accepted(client: TestClient) -> None:
    """A pre-Phase-8 v1 snapshot still validates on the way in (the client
    migrates its shape on load); an unknown version is still rejected."""
    created = client.post("/api/workspaces", json={"name": "Legacy", "state": _V1_STATE})
    assert created.status_code == 201
    assert created.json()["state"]["v"] == 1
    assert created.json()["state"]["date"]["mode"] == "range"


def test_unknown_version_rejected_by_route(client: TestClient) -> None:
    bad = client.post("/api/workspaces", json={"name": "V99", "state": _state(v=99)})
    assert bad.status_code == 422


def test_state_schema_round_trip_and_version_guard() -> None:
    state = WorkspaceState.model_validate(_state())
    assert state.v == 2
    # Re-validating its own dump is a fixed point (round-trip stable).
    assert WorkspaceState.model_validate(state.model_dump(mode="json")) == state
    # v1 still validates (accepted for migration); an unknown version does not.
    assert WorkspaceState.model_validate(_V1_STATE).v == 1
    with pytest.raises(ValidationError):
        WorkspaceState.model_validate(_state(v=99))
