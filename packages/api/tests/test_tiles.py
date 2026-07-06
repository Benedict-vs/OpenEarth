"""POST /api/tiles with the EE seam faked at the service-module level."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openearth.catalog import parse_dataset_toml, register_dataset
from openearth.ee.render import TileRef
from openearth.geometry import BBox, PolygonROI
from openearth_api.deps import ensure_ee
from openearth_api.services import tiles as tiles_service

FAKE_URL = "https://earthengine.example/{z}/{x}/{y}"
HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}
DATES = {"start": "2024-06-01", "end": "2024-07-01"}

DEM_TOML = """
[dataset]
id = "dem"
title = "Copernicus DEM GLO-30"
collection_id = "COPERNICUS/DEM/GLO30"
attribution = "ESA"
default_scale_m = 30

[products.DEM]
name = "Elevation"
vis_min = 0
vis_max = 4000
valid_min = -500
valid_max = 9000
display_unit = "m"
"""


@pytest.fixture
def seams(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake EE at the names imported into the tiles service; record calls."""
    app.dependency_overrides[ensure_ee] = lambda: None
    calls: dict[str, Any] = {}

    def fake_mean(data_key: str, roi: Any, start: Any, end: Any, source: str) -> str:
        calls["build"] = ("mean", data_key, roi, start, end, source)
        return "fake-image"

    def fake_window(
        data_key: str, roi: Any, target_date: Any, half_window_days: int, source: str
    ) -> str:
        calls["build"] = ("date_window", data_key, roi, target_date, half_window_days, source)
        return "fake-image"

    def fake_single(data_key: str, roi: Any, timestamp_ms: int, source: str) -> str:
        calls["build"] = ("single_scene", data_key, roi, timestamp_ms, source)
        return "fake-image"

    def fake_mint(image: Any, spec: Any, **kwargs: Any) -> TileRef:
        calls["mint"] = {"image": image, "spec": spec, **kwargs}
        return TileRef(
            url=FAKE_URL,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=4),
            attribution=kwargs.get("attribution", "test"),
        )

    monkeypatch.setattr(tiles_service, "build_mean_composite", fake_mean)
    monkeypatch.setattr(tiles_service, "build_date_composite", fake_window)
    monkeypatch.setattr(tiles_service, "build_single_scene", fake_single)
    monkeypatch.setattr(tiles_service, "mint_tile_url", fake_mint)
    return calls


def test_mean_composite_with_bbox(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post(
        "/api/tiles",
        json={"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, "dates": DATES},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tile_url"] == FAKE_URL
    assert body["attribution"] == "Google Earth Engine / Copernicus Sentinel-2"

    mode, data_key, roi, start, end, source = seams["build"]
    assert mode == "mean"
    assert data_key == "NDVI"
    assert isinstance(roi, BBox)  # real domain object reached the builder
    assert (roi.west, roi.north) == (8.58, 49.46)
    assert (str(start), str(end)) == ("2024-06-01", "2024-07-01")
    assert source == "s2"


def test_polygon_roi_reaches_builder(client: TestClient, seams: dict[str, Any]) -> None:
    polygon = {
        "kind": "polygon",
        "coordinates": [[8.6, 49.3], [8.8, 49.3], [8.8, 49.5], [8.6, 49.5]],
    }
    response = client.post(
        "/api/tiles",
        json={"dataset": "s5p", "product": "NO2", "roi": polygon, "dates": DATES},
    )
    assert response.status_code == 200
    roi = seams["build"][2]
    assert isinstance(roi, PolygonROI)
    assert roi.ring[0] == (8.6, 49.3)


def test_missing_roi_defaults_to_global(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post("/api/tiles", json={"dataset": "s5p", "product": "NO2", "dates": DATES})
    assert response.status_code == 200
    assert seams["build"][2].is_global


def test_date_window_mode(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post(
        "/api/tiles",
        json={
            "dataset": "s2",
            "product": "NDVI",
            "roi": HEIDELBERG,
            "composite": "date_window",
            "target_date": "2024-06-15",
            "half_window_days": 5,
        },
    )
    assert response.status_code == 200
    mode, _, _, target_date, half_window, _ = seams["build"]
    assert mode == "date_window"
    assert str(target_date) == "2024-06-15"
    assert half_window == 5


def test_single_scene_mode(client: TestClient, seams: dict[str, Any]) -> None:
    ts = 1_718_450_000_000
    response = client.post(
        "/api/tiles",
        json={
            "dataset": "s2",
            "product": "NDVI",
            "roi": HEIDELBERG,
            "composite": "single_scene",
            "timestamp_ms": ts,
        },
    )
    assert response.status_code == 200
    assert seams["build"][:1] == ("single_scene",)
    assert seams["build"][3] == ts


@pytest.mark.parametrize(
    ("payload_patch", "missing"),
    [
        ({"composite": "mean"}, "dates"),
        ({"composite": "date_window"}, "target_date"),
        ({"composite": "single_scene"}, "timestamp_ms"),
    ],
)
def test_missing_mode_params_are_422(
    client: TestClient, seams: dict[str, Any], payload_patch: dict[str, Any], missing: str
) -> None:
    response = client.post(
        "/api/tiles",
        json={"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, **payload_patch},
    )
    assert response.status_code == 422
    assert missing in response.json()["detail"]


def test_legend_reflects_spec_and_overrides(client: TestClient, seams: dict[str, Any]) -> None:
    base = {"dataset": "s5p", "product": "NO2", "roi": HEIDELBERG, "dates": DATES}
    legend = client.post("/api/tiles", json=base).json()["legend"]
    assert legend["unit"]
    assert len(legend["palette"]) >= 2
    overridden = client.post(
        "/api/tiles", json={**base, "viz_overrides": {"vis_min": 1.0, "vis_max": 9.0}}
    ).json()["legend"]
    assert (overridden["min"], overridden["max"]) == (1.0, 9.0)
    # Overrides must also reach the mint call.
    assert seams["mint"]["vis_min"] == 1.0


def test_unknown_dataset_and_product_are_404(client: TestClient, seams: dict[str, Any]) -> None:
    for payload in (
        {"dataset": "nope", "product": "NDVI", "dates": DATES},
        {"dataset": "s2", "product": "NOPE", "dates": DATES},
    ):
        assert client.post("/api/tiles", json=payload).status_code == 404


def test_malformed_roi_is_422_with_domain_message(
    client: TestClient, seams: dict[str, Any]
) -> None:
    bad_bbox = {**HEIDELBERG, "east": 8.0}  # east < west
    response = client.post(
        "/api/tiles",
        json={"dataset": "s2", "product": "NDVI", "roi": bad_bbox, "dates": DATES},
    )
    assert response.status_code == 422
    assert "width" in response.json()["detail"]

    two_points = {"kind": "polygon", "coordinates": [[8.0, 49.0], [9.0, 50.0]]}
    response = client.post(
        "/api/tiles",
        json={"dataset": "s2", "product": "NDVI", "roi": two_points, "dates": DATES},
    )
    assert response.status_code == 422


def test_reversed_dates_are_422(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post(
        "/api/tiles",
        json={
            "dataset": "s2",
            "product": "NDVI",
            "roi": HEIDELBERG,
            "dates": {"start": "2024-07-01", "end": "2024-06-01"},
        },
    )
    assert response.status_code == 422
    assert "after" in response.json()["detail"]


def test_builder_product_refused_without_methane_ref(
    client: TestClient, seams: dict[str, Any]
) -> None:
    # The CH4_ANOMALY builder still 422s without the 'methane_ref' unlock.
    response = client.post(
        "/api/tiles",
        json={"dataset": "s2", "product": "CH4_ANOMALY", "roi": HEIDELBERG, "dates": DATES},
    )
    assert response.status_code == 422
    assert "methane_ref" in response.json()["detail"]
    assert "build" not in seams  # refused before any composite work


def test_custom_dataset_mints_through_generic_path(
    client: TestClient, seams: dict[str, Any]
) -> None:
    register_dataset(parse_dataset_toml(DEM_TOML))
    response = client.post(
        "/api/tiles",
        json={"dataset": "dem", "product": "DEM", "roi": HEIDELBERG, "dates": DATES},
    )
    assert response.status_code == 200
    assert response.json()["attribution"] == "ESA"
    assert seams["build"][5] == "dem"  # source routes to the generic provider
