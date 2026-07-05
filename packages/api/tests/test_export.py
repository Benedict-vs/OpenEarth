"""Export endpoints with EE + GeoTIFF assembly faked at the service level.

``build_mean_composite`` (the image the export writes) and ``export_geotiff``
(the windowed rasterio write) are both monkeypatched, so submit → job → download
runs offline. Job completion is observed by polling ``GET /jobs/{id}`` — the
manager runs the runner on the TestClient's event loop.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openearth.geometry import BBox
from openearth_api.deps import ensure_ee
from openearth_api.services import export as export_service
from openearth_api.services import tiles as tiles_service

if TYPE_CHECKING:
    from fastapi import FastAPI

HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}
DATES = {"start": "2024-06-01", "end": "2024-07-01"}
GEOTIFF_BODY = {"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, "dates": DATES}

_TIFF_BYTES = b"II*\x00fake-geotiff-bytes"


@pytest.fixture
def seams(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the composite builder and the GeoTIFF writer; ``gate`` gates the write."""
    app.dependency_overrides[ensure_ee] = lambda: None
    state: dict[str, Any] = {"gate": threading.Event(), "export": None, "png_calls": 0}
    state["gate"].set()  # instant unless a test clears it

    def fake_mean(data_key: str, roi: Any, start: Any, end: Any, source: str) -> str:
        return "fake-image"

    def fake_export(
        image: Any,
        product_spec: Any,
        roi: Any,
        scale_m: int,
        dest: Path,
        *,
        on_progress: Any = None,
    ) -> Path:
        state["export"] = {"image": image, "roi": roi, "scale_m": scale_m, "dest": dest}
        state["gate"].wait(5.0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_TIFF_BYTES)
        if on_progress is not None:
            on_progress(1, 1)
        return dest

    def fake_thumbnail(req: Any, cache: Any) -> bytes:
        state["png_calls"] += 1
        return b"PNGDATA"

    monkeypatch.setattr(tiles_service, "build_mean_composite", fake_mean)
    monkeypatch.setattr(export_service, "export_geotiff", fake_export)
    monkeypatch.setattr(export_service, "render_thumbnail", fake_thumbnail)
    return state


def _wait_status(client: TestClient, job_id: str, status: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] == status:
            return
        if body["status"] in {"failed", "cancelled", "interrupted"} and status == "succeeded":
            raise AssertionError(f"job ended {body['status']}: {body.get('error')}")
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {status}")


# ── GeoTIFF: submit → job → download ─────────────────────────


def test_geotiff_export_writes_and_serves_file(client: TestClient, seams: dict[str, Any]) -> None:
    job_id = client.post("/api/export/geotiff", json=GEOTIFF_BODY).json()["job_id"]
    _wait_status(client, job_id, "succeeded")

    # The writer received the built composite, the domain ROI, and native scale.
    assert seams["export"]["image"] == "fake-image"
    assert isinstance(seams["export"]["roi"], BBox)
    assert seams["export"]["scale_m"] == 100  # s2 default_scale_m

    response = client.get(f"/api/export/{job_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/tiff"
    assert 'filename="s2_NDVI_2024-06-01_2024-07-01.tif"' in response.headers["content-disposition"]
    assert response.content == _TIFF_BYTES


def test_scale_m_override_is_honoured(client: TestClient, seams: dict[str, Any]) -> None:
    body = {**GEOTIFF_BODY, "scale_m": 500}
    job_id = client.post("/api/export/geotiff", json=body).json()["job_id"]
    _wait_status(client, job_id, "succeeded")
    assert seams["export"]["scale_m"] == 500


def test_download_before_finished_is_409(client: TestClient, seams: dict[str, Any]) -> None:
    seams["gate"].clear()  # the writer blocks, so the job stays unfinished
    job_id = client.post("/api/export/geotiff", json=GEOTIFF_BODY).json()["job_id"]

    response = client.get(f"/api/export/{job_id}/download")
    assert response.status_code == 409

    seams["gate"].set()  # let it finish so teardown is clean
    _wait_status(client, job_id, "succeeded")


def test_download_unknown_job_is_404(client: TestClient, seams: dict[str, Any]) -> None:
    assert client.get("/api/export/nope/download").status_code == 404


def test_download_after_file_removed_is_410(client: TestClient, seams: dict[str, Any]) -> None:
    job_id = client.post("/api/export/geotiff", json=GEOTIFF_BODY).json()["job_id"]
    _wait_status(client, job_id, "succeeded")

    seams["export"]["dest"].unlink()  # simulate an evicted export
    assert client.get(f"/api/export/{job_id}/download").status_code == 410


# ── request validation (before any job) ──────────────────────


def test_missing_roi_is_422(client: TestClient, seams: dict[str, Any]) -> None:
    body = {"dataset": "s2", "product": "NDVI", "dates": DATES}
    assert client.post("/api/export/geotiff", json=body).status_code == 422


def test_unknown_dataset_is_404(client: TestClient, seams: dict[str, Any]) -> None:
    body = {**GEOTIFF_BODY, "dataset": "nope"}
    assert client.post("/api/export/geotiff", json=body).status_code == 404


def test_builder_product_is_422(client: TestClient, seams: dict[str, Any]) -> None:
    body = {**GEOTIFF_BODY, "product": "CH4_ANOMALY"}
    response = client.post("/api/export/geotiff", json=body)
    assert response.status_code == 422
    assert "Phase 3" in response.json()["detail"]


# ── PNG: synchronous attachment ──────────────────────────────


def test_png_export_is_attachment(client: TestClient, seams: dict[str, Any]) -> None:
    response = client.post("/api/export/png", json={**GEOTIFF_BODY, "width": 2048})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert 'filename="s2_NDVI_2024-06-01_2024-07-01.png"' in response.headers["content-disposition"]
    assert response.content == b"PNGDATA"
    assert seams["png_calls"] == 1
