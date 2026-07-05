"""POST /api/inspect with the EE seam faked at the service-module level.

The composite builder (imported into the tiles service) and the point sampler
(imported into the inspect service) are both monkeypatched, so the whole
request/response path runs offline with no Earth Engine.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from openearth.geometry import BBox
from openearth_api.deps import ensure_ee
from openearth_api.services import inspect as inspect_service
from openearth_api.services import tiles as tiles_service

HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}
DATES = {"start": "2024-06-01", "end": "2024-07-01"}
POINT = {"lon": 8.68, "lat": 49.41}


@pytest.fixture
def seams(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the mean composite builder and the point sampler; record calls."""
    app.dependency_overrides[ensure_ee] = lambda: None
    calls: dict[str, Any] = {}

    def fake_mean(data_key: str, roi: Any, start: Any, end: Any, source: str) -> str:
        calls["build"] = ("mean", data_key, roi, start, end, source)
        return "fake-image"

    def fake_sample(image: Any, band: str, lon: float, lat: float, scale_m: int) -> float | None:
        calls["sample"] = {
            "image": image,
            "band": band,
            "lon": lon,
            "lat": lat,
            "scale_m": scale_m,
        }
        return calls.get("value", 0.42)

    monkeypatch.setattr(tiles_service, "build_mean_composite", fake_mean)
    monkeypatch.setattr(inspect_service, "sample_point", fake_sample)
    return calls


def _post(client: TestClient, **patch: Any) -> Response:
    body = {"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, "dates": DATES, **POINT, **patch}
    return client.post("/api/inspect", json=body)


def test_samples_the_current_composite(client: TestClient, seams: dict[str, Any]) -> None:
    response = _post(client)
    assert response.status_code == 200
    assert response.json() == {
        "value": 0.42,
        "band": "NDVI",
        "unit": "index",
        "display_scale": 1.0,
    }

    # The sampler received the built composite image, the product band, the
    # click point, and the dataset's native scale.
    sample = seams["sample"]
    assert sample["image"] == "fake-image"
    assert sample["band"] == "NDVI"
    assert (sample["lon"], sample["lat"]) == (8.68, 49.41)
    assert sample["scale_m"] == 100  # s2 default_scale_m

    # The composite builder saw the real domain ROI (not raw JSON).
    assert isinstance(seams["build"][2], BBox)


def test_masked_pixel_returns_null_value(client: TestClient, seams: dict[str, Any]) -> None:
    seams["value"] = None
    response = _post(client)
    assert response.status_code == 200
    assert response.json()["value"] is None


def test_rgb_product_is_422_before_building(client: TestClient, seams: dict[str, Any]) -> None:
    response = _post(client, product="RGB")
    assert response.status_code == 422
    assert "scalar" in response.json()["detail"]
    # Refused before any composite build or sample.
    assert "build" not in seams
    assert "sample" not in seams


def test_unknown_dataset_and_product_are_404(client: TestClient, seams: dict[str, Any]) -> None:
    assert _post(client, dataset="nope").status_code == 404
    assert _post(client, product="NOPE").status_code == 404


def test_builder_product_is_422(client: TestClient, seams: dict[str, Any]) -> None:
    response = _post(client, product="CH4_ANOMALY")
    assert response.status_code == 422
    assert "Phase 3" in response.json()["detail"]


def test_missing_mode_params_propagate_422(client: TestClient, seams: dict[str, Any]) -> None:
    # composite defaults to "mean"; omitting dates must 422 like tiles do,
    # before any composite work.
    body = {"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, **POINT}
    response = client.post("/api/inspect", json=body)
    assert response.status_code == 422
    assert "dates" in response.json()["detail"]
    assert "sample" not in seams
