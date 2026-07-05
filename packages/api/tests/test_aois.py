"""Saved-AOI CRUD over a tmp DB (via the TestClient lifespan). No EE."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BBOX = {"kind": "bbox", "west": -103.0, "south": 31.5, "east": -102.0, "north": 32.5}
POLYGON = {
    "kind": "polygon",
    "coordinates": [[8.6, 49.3], [8.8, 49.35], [8.7, 49.5], [8.6, 49.3]],
}


def test_create_list_delete_round_trip(client: TestClient) -> None:
    assert client.get("/api/aois").json() == []

    created = client.post("/api/aois", json={"name": "Permian", "roi": BBOX})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Permian"
    assert body["roi"] == BBOX
    assert isinstance(body["id"], int)
    assert body["created_at"]

    listed = client.get("/api/aois").json()
    assert [a["name"] for a in listed] == ["Permian"]

    assert client.delete(f"/api/aois/{body['id']}").status_code == 204
    assert client.get("/api/aois").json() == []


def test_polygon_roi_survives_round_trip(client: TestClient) -> None:
    created = client.post("/api/aois", json={"name": "Heidelberg", "roi": POLYGON})
    assert created.status_code == 201
    assert created.json()["roi"] == POLYGON


def test_duplicate_name_is_409(client: TestClient) -> None:
    assert client.post("/api/aois", json={"name": "Dup", "roi": BBOX}).status_code == 201
    clash = client.post("/api/aois", json={"name": "Dup", "roi": BBOX})
    assert clash.status_code == 409
    assert "already exists" in clash.json()["detail"]


def test_delete_unknown_is_404(client: TestClient) -> None:
    assert client.delete("/api/aois/9999").status_code == 404


def test_list_is_sorted_by_name(client: TestClient) -> None:
    for name in ("Zulu", "Alpha", "Mike"):
        client.post("/api/aois", json={"name": name, "roi": BBOX})
    assert [a["name"] for a in client.get("/api/aois").json()] == ["Alpha", "Mike", "Zulu"]


def test_blank_name_is_422(client: TestClient) -> None:
    assert client.post("/api/aois", json={"name": "", "roi": BBOX}).status_code == 422


def test_invalid_roi_is_422(client: TestClient) -> None:
    bad = {"kind": "bbox", "west": 10.0, "south": 1.0, "east": 5.0, "north": 2.0}  # west > east
    assert client.post("/api/aois", json={"name": "Bad", "roi": bad}).status_code == 422
