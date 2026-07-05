"""POST /api/thumbnail: PNG streaming and the diskcache tier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openearth_api.cache import OPEN_ENDED_TTL_SECONDS
from openearth_api.deps import ensure_ee
from openearth_api.services import thumbnails as thumb_service
from openearth_api.services import tiles as tiles_service

HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}
PNG_BYTES = b"\x89PNG-fake"


@pytest.fixture
def seams(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    app.dependency_overrides[ensure_ee] = lambda: None
    calls: dict[str, Any] = {"fetches": 0, "expires": []}

    monkeypatch.setattr(tiles_service, "build_mean_composite", lambda *a, **kw: "fake-image")
    monkeypatch.setattr(thumb_service, "thumb_url", lambda *a, **kw: "https://ee.example/thumb.png")

    def fake_fetch(url: str) -> bytes:
        calls["fetches"] += 1
        return PNG_BYTES

    monkeypatch.setattr(thumb_service, "_fetch_bytes", fake_fetch)

    real_ttl_for = thumb_service.ttl_for

    def spy_ttl(end: Any) -> int | None:
        ttl = real_ttl_for(end)
        calls["expires"].append(ttl)
        return ttl

    monkeypatch.setattr(thumb_service, "ttl_for", spy_ttl)
    return calls


def _payload(dates: dict[str, str]) -> dict[str, Any]:
    return {"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, "dates": dates}


def test_thumbnail_streams_png(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post(
        "/api/thumbnail", json=_payload({"start": "2024-06-01", "end": "2024-07-01"})
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_BYTES


def test_second_identical_call_hits_cache(client: TestClient, seams: dict[str, Any]) -> None:
    payload = _payload({"start": "2024-06-01", "end": "2024-07-01"})
    assert client.post("/api/thumbnail", json=payload).content == PNG_BYTES
    assert client.post("/api/thumbnail", json=payload).content == PNG_BYTES
    assert seams["fetches"] == 1  # second call served from diskcache


def test_different_width_misses_cache(client: TestClient, seams: dict[str, Any]) -> None:
    payload = _payload({"start": "2024-06-01", "end": "2024-07-01"})
    client.post("/api/thumbnail", json=payload)
    client.post("/api/thumbnail", json={**payload, "width": 512})
    assert seams["fetches"] == 2


def test_closed_range_cached_forever(client: TestClient, seams: dict[str, Any]) -> None:
    client.post("/api/thumbnail", json=_payload({"start": "2024-06-01", "end": "2024-07-01"}))
    assert seams["expires"] == [None]


def test_open_ended_range_gets_short_ttl(client: TestClient, seams: dict[str, Any]) -> None:
    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).date().isoformat()
    client.post("/api/thumbnail", json=_payload({"start": "2024-06-01", "end": tomorrow}))
    assert seams["expires"] == [OPEN_ENDED_TTL_SECONDS]
