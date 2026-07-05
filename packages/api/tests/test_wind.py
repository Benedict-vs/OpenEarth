"""GET /api/wind and /api/wind/field with the EE samplers faked at the service.

``sample_wind_at`` / ``sample_wind_field`` are monkeypatched in the wind service
module, so the whole request/response path — routing, bbox construction, the
aspect-derived ``ny`` default, and NaN→null shaping — runs offline.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openearth.geometry import BBox
from openearth.methane.wind import WindField, WindSample
from openearth_api.deps import ensure_ee
from openearth_api.services import wind as wind_service
from openearth_api.services.wind import ny_from_aspect

BOX = {"west": -103.0, "south": 31.5, "east": -102.0, "north": 32.5}
TIME = "2024-07-15T12:00:00Z"


@pytest.fixture
def seams(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake both wind samplers; record how they were called."""
    app.dependency_overrides[ensure_ee] = lambda: None
    calls: dict[str, Any] = {"field_n": 0, "point_n": 0}

    def fake_field(
        bbox: Any,
        when: Any,
        nx: int,
        ny: int,
        *,
        collection_id: str = "",
        fallback_collection_id: Any = None,
    ) -> WindField:
        calls["field_n"] += 1
        calls["field"] = {
            "bbox": bbox,
            "when": when,
            "nx": nx,
            "ny": ny,
            "fallback": fallback_collection_id,
        }
        n = nx * ny
        u = [float(i) for i in range(n)]
        v = [-float(i) for i in range(n)]
        if n > 1:  # mask one cell to exercise NaN → null at the boundary
            u[1] = math.nan
            v[1] = math.nan
        return WindField(
            when=when,
            bbox=bbox,
            nx=nx,
            ny=ny,
            u=tuple(u),
            v=tuple(v),
            collection_id="ECMWF/ERA5_LAND/HOURLY",
        )

    def fake_point(
        roi: Any, when: Any, *, collection_id: str = "", fallback_collection_id: Any = None
    ) -> WindSample:
        calls["point_n"] += 1
        calls["point"] = {"roi": roi, "when": when, "fallback": fallback_collection_id}
        return WindSample.from_uv(when, 3.0, 4.0, "ECMWF/ERA5_LAND/HOURLY")

    monkeypatch.setattr(wind_service, "sample_wind_field", fake_field)
    monkeypatch.setattr(wind_service, "sample_wind_at", fake_point)
    return calls


# ── /wind/field ──────────────────────────────────────────────


def _field(client: TestClient, **params: Any) -> Any:
    query = {**BOX, "time": TIME, **params}
    return client.get("/api/wind/field", params=query)


def test_field_returns_row_major_arrays_with_masked_nulls(
    client: TestClient, seams: dict[str, Any]
) -> None:
    response = _field(client, nx=4, ny=3)
    assert response.status_code == 200
    body = response.json()
    assert (body["nx"], body["ny"]) == (4, 3)
    assert len(body["u"]) == 12
    assert len(body["v"]) == 12
    assert body["u"][0] == 0.0
    assert body["u"][1] is None  # masked cell crossed the boundary as null
    assert body["v"][1] is None
    assert body["collection_id"] == "ECMWF/ERA5_LAND/HOURLY"
    # The sampler saw the real domain bbox and the parsed instant.
    assert isinstance(seams["field"]["bbox"], BBox)
    assert seams["field"]["fallback"] == "ECMWF/ERA5/HOURLY"


def test_field_ny_defaults_from_aspect(client: TestClient, seams: dict[str, Any]) -> None:
    response = _field(client, nx=10)  # ny omitted
    assert response.status_code == 200
    expected_ny = ny_from_aspect(BBox(**BOX), 10)
    assert response.json()["ny"] == expected_ny
    assert len(response.json()["u"]) == 10 * expected_ny


def test_field_echoes_request_bbox(client: TestClient, seams: dict[str, Any]) -> None:
    body = _field(client, nx=2, ny=2).json()
    assert body["bbox"] == {"kind": "bbox", **BOX}


def test_field_caches_second_identical_request(client: TestClient, seams: dict[str, Any]) -> None:
    _field(client, nx=3, ny=3)
    _field(client, nx=3, ny=3)
    assert seams["field_n"] == 1  # served from the disk cache the second time


def test_field_invalid_bbox_is_422(client: TestClient, seams: dict[str, Any]) -> None:
    # west east swapped → InvalidROIError → 422, before any sampling.
    response = client.get(
        "/api/wind/field",
        params={"west": 5.0, "south": 0.0, "east": -5.0, "north": 10.0, "time": TIME},
    )
    assert response.status_code == 422
    assert seams["field_n"] == 0


@pytest.mark.parametrize("nx", [0, 51])
def test_field_nx_out_of_range_is_422(client: TestClient, seams: dict[str, Any], nx: int) -> None:
    assert _field(client, nx=nx).status_code == 422
    assert seams["field_n"] == 0


# ── /wind (point) ────────────────────────────────────────────


def test_point_mirrors_wind_sample(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.get("/api/wind", params={"lat": 32.0, "lon": -102.5, "time": TIME})
    assert response.status_code == 200
    body = response.json()
    assert body["speed_ms"] == pytest.approx(5.0)  # hypot(3, 4)
    assert body["collection_id"] == "ECMWF/ERA5_LAND/HOURLY"
    # A small box was built around the point and the global fallback supplied.
    roi = seams["point"]["roi"]
    assert isinstance(roi, BBox)
    assert roi.west == pytest.approx(-102.55)
    assert roi.east == pytest.approx(-102.45)
    assert seams["point"]["fallback"] == "ECMWF/ERA5/HOURLY"


def test_point_lat_out_of_range_is_422(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.get("/api/wind", params={"lat": 999, "lon": 0, "time": TIME})
    assert response.status_code == 422
    assert seams["point_n"] == 0
