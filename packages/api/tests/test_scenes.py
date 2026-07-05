"""POST /api/scenes with list_acquisition_times faked."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openearth.geometry import BBox
from openearth_api.deps import ensure_ee
from openearth_api.routers import scenes as scenes_router

HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}
T1 = datetime(2024, 6, 10, 10, 30, tzinfo=UTC)
T2 = datetime(2024, 6, 15, 10, 30, tzinfo=UTC)


@pytest.fixture
def seams(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    app.dependency_overrides[ensure_ee] = lambda: None
    calls: dict[str, Any] = {}

    def fake_times(data_key: str, roi: Any, start: Any, end: Any, source: str) -> list[datetime]:
        calls["args"] = (data_key, roi, start, end, source)
        return [T1, T2]

    monkeypatch.setattr(scenes_router, "list_acquisition_times", fake_times)
    return calls


def test_scenes_listed_with_timestamps(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post(
        "/api/scenes",
        json={
            "dataset": "s2",
            "product": "NDVI",
            "roi": HEIDELBERG,
            "dates": {"start": "2024-06-01", "end": "2024-07-01"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [s["timestamp_ms"] for s in body] == [
        int(T1.timestamp() * 1000),
        int(T2.timestamp() * 1000),
    ]
    data_key, roi, _, _, source = seams["args"]
    assert (data_key, source) == ("NDVI", "s2")
    assert isinstance(roi, BBox)


def test_scenes_validation(client: TestClient, seams: dict[str, Any]) -> None:
    base = {"roi": HEIDELBERG, "dates": {"start": "2024-06-01", "end": "2024-07-01"}}
    assert (
        client.post("/api/scenes", json={"dataset": "nope", "product": "NDVI", **base}).status_code
        == 404
    )
    reversed_dates = {"start": "2024-07-01", "end": "2024-06-01"}
    assert (
        client.post(
            "/api/scenes",
            json={"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, "dates": reversed_dates},
        ).status_code
        == 422
    )
