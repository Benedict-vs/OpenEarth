"""Health and config endpoints (EE uninitialized)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_config_reports_uninitialized_ee_and_cache_stats(client: TestClient) -> None:
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert body["ee_project"] is None
    assert body["ee_initialized"] is False
    assert "OPENEARTH_EE_PROJECT" in body["ee_error"]
    assert body["tile_ttl_seconds"] == 4 * 3600
    assert body["cache"] == {"count": 0, "volume_bytes": body["cache"]["volume_bytes"]}
