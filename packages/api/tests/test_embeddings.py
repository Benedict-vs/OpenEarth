"""Embeddings Explorer routes — year validation, k clamping, seed echo (EE faked)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openearth.ee.render import TileRef
from openearth.errors import EmptyCollectionError
from openearth_api.deps import ensure_ee
from openearth_api.services import embeddings as svc

if TYPE_CHECKING:
    from fastapi import FastAPI

_YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
_HD = {"kind": "bbox", "west": 8.6, "south": 49.35, "east": 8.75, "north": 49.45}


@pytest.fixture
def faked(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override EE + core fns so routes run with no Earth Engine."""
    app.dependency_overrides[ensure_ee] = lambda: None
    monkeypatch.setattr(svc, "available_years", lambda: list(_YEARS))
    # Unit-norm seed (‖v‖ = 1) at 64 dims → seed_norm echo ≈ 1.0.
    monkeypatch.setattr(svc, "seed_vector", lambda lat, lon, year: [0.125] * 64)
    monkeypatch.setattr(svc, "similarity_image", lambda seed, year: object())
    monkeypatch.setattr(svc, "change_image", lambda a, b: object())
    monkeypatch.setattr(svc, "cluster_image", lambda bbox, year, k, **kw: object())
    monkeypatch.setattr(
        svc,
        "mint_tile_url",
        lambda image, spec, **kw: TileRef(
            url="https://ee/{z}/{x}/{y}",
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            attribution=kw.get("attribution", "x"),
        ),
    )


def test_years_route(client: TestClient, faked: None) -> None:
    body = client.get("/api/embeddings/years").json()
    assert body["years"] == _YEARS


def test_similarity_seed_echo_and_tile(client: TestClient, faked: None) -> None:
    resp = client.post("/api/embeddings/similarity", json={"lat": 49.41, "lon": 8.68, "year": 2023})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tile_url"] == "https://ee/{z}/{x}/{y}"
    assert "AlphaEarth" in body["attribution"]  # CC-BY attribution echoed
    assert body["seed_norm"] == pytest.approx(1.0)  # unit-norm seed sanity
    assert body["legend"]["min"] == -0.2
    assert body["legend"]["max"] == 1.0


def test_similarity_rejects_unavailable_year(client: TestClient, faked: None) -> None:
    resp = client.post("/api/embeddings/similarity", json={"lat": 49.41, "lon": 8.68, "year": 2016})
    assert resp.status_code == 422
    assert "2017" in resp.json()["detail"]


def test_similarity_seed_out_of_coverage_404(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch, faked: None
) -> None:
    def raise_empty(lat: float, lon: float, year: int) -> Any:
        raise EmptyCollectionError("No AlphaEarth embedding at this point.")

    monkeypatch.setattr(svc, "seed_vector", raise_empty)
    resp = client.post("/api/embeddings/similarity", json={"lat": 0, "lon": 0, "year": 2023})
    assert resp.status_code == 404


def test_change_validates_both_years(client: TestClient, faked: None) -> None:
    ok = client.post("/api/embeddings/change", json={"year_a": 2018, "year_b": 2023})
    assert ok.status_code == 200
    bad = client.post("/api/embeddings/change", json={"year_a": 2018, "year_b": 2099})
    assert bad.status_code == 422


def test_cluster_clamps_k(client: TestClient, faked: None) -> None:
    hi = client.post("/api/embeddings/cluster", json={"roi": _HD, "year": 2023, "k": 100})
    assert hi.status_code == 200
    assert hi.json()["n_clusters"] == 12  # clamped to K_MAX
    lo = client.post("/api/embeddings/cluster", json={"roi": _HD, "year": 2023, "k": 1})
    assert lo.json()["n_clusters"] == 2  # clamped to K_MIN
    # Legend palette is truncated to the actual cluster count.
    assert len(hi.json()["legend"]["palette"]) == 12
    assert len(lo.json()["legend"]["palette"]) == 2


def test_cluster_requires_roi(client: TestClient, faked: None) -> None:
    resp = client.post("/api/embeddings/cluster", json={"year": 2023, "k": 6})
    assert resp.status_code == 422  # roi is required (training needs a region)
