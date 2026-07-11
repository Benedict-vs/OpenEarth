"""Stage 5 — ML scan API (ORT session + EE faked by name in services.ml)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openearth.ee.pixels import grid_for
from openearth.geometry import BBox
from openearth.methane.channels import CHANNELS
from openearth.methane.retrieval import RetrievalChip
from openearth.methane.scenes import S2Scene
from openearth.methane.wind import WindSample
from openearth_api.deps import ensure_ee
from openearth_api.services import ml as svc_ml

if TYPE_CHECKING:
    from fastapi import FastAPI


def _wait_succeeded(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] == "succeeded":
            return body
        if body["status"] in {"failed", "cancelled", "interrupted"}:
            raise AssertionError(f"job ended {body['status']}: {body.get('error')}")
        time.sleep(0.01)
    raise AssertionError("job did not succeed")


def _scene(sid: str) -> S2Scene:
    return S2Scene(sid, datetime(2021, 7, 1, 7, 46, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0)


def _chip(scene: S2Scene) -> RetrievalChip:
    g = grid_for(BBox(53.7, 38.2, 53.72, 38.22), 20)
    r12 = np.full((g.height, g.width), 0.2, np.float32)
    r11 = (1.02 * r12).astype(np.float32)
    bands = {"B11": r11, "B12": r12, "B4": r12, "B3": r12, "B2": r12}
    return RetrievalChip(scene=scene, grid=g, bands=bands)


class _FakeSession:
    """Emits logits with a positive blob so candidates_from_prob finds one plume."""

    def get_inputs(self) -> list[Any]:
        return [SimpleNamespace(name="input")]

    def run(self, _outputs: Any, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        x = next(iter(feed.values()))  # (1, 5, H, W)
        logits = np.full((1, 1, x.shape[2], x.shape[3]), -6.0, np.float32)
        logits[0, 0, 4:16, 4:16] = 6.0
        return [logits]


def _fake_model() -> dict[str, Any]:
    return {
        "session": _FakeSession(),
        "manifest": {
            "model_version": "plume_unet_v1",
            "threshold": 0.5,
            "min_px": 5,
            "latency_ms_p50": 15.6,
        },
        "stats": SimpleNamespace(channels=CHANNELS),  # normalize reads .channels/.median/.mad
    }


def _wind() -> WindSample:
    return WindSample(
        when=datetime(2021, 7, 1, tzinfo=UTC),
        u_ms=3.0,
        v_ms=1.0,
        speed_ms=3.16,
        wind_to_deg=70.0,
        wind_from_deg=250.0,
        collection_id="fake",
    )


@pytest.fixture
def scan_ready(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    from openearth.methane.channels import ChannelStats

    stats = ChannelStats(CHANNELS, (0.0,) * 5, (1.0,) * 5)
    model = _fake_model()
    model["stats"] = stats
    monkeypatch.setattr(svc_ml, "load_model", lambda _s: model)
    monkeypatch.setattr(svc_ml, "list_scenes", lambda *a, **k: [_scene("A"), _scene("B")])
    monkeypatch.setattr(svc_ml, "pick_reference", lambda target, cands: _scene("REF"))
    monkeypatch.setattr(svc_ml, "fetch_chip", lambda scene, bbox, **k: _chip(scene))
    monkeypatch.setattr(svc_ml, "sample_wind_at", lambda *a, **k: _wind())


def test_ml_status_absent_when_no_model(client: TestClient) -> None:
    body = client.get("/api/methane/ml/status").json()
    assert body["model_loaded"] is False
    assert body["model_version"] is None


# A chip-sized sub-area — seeded site ROIs are browse-scale and exceed the 20 m limit.
SCAN_ROI = {"kind": "bbox", "west": 53.9, "south": 38.4, "east": 54.0, "north": 38.5}


def test_ml_scan_requires_installed_model(client: TestClient, app: FastAPI) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    resp = client.post(
        "/api/methane/ml/scan",
        json={"site_id": site_id, "roi": SCAN_ROI, "start": "2021-07-01", "end": "2021-08-01"},
    )
    assert resp.status_code == 503  # model file not installed in the test data_dir


def test_ml_scan_rejects_oversized_bbox_at_submit(client: TestClient, scan_ready: None) -> None:
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    resp = client.post(
        "/api/methane/ml/scan",
        json={"site_id": site_id, "start": "2021-07-01", "end": "2021-08-01"},
    )
    assert resp.status_code == 422
    assert "Refusing" in resp.json()["detail"]


def test_ml_scan_end_to_end(client: TestClient, scan_ready: None) -> None:
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    resp = client.post(
        "/api/methane/ml/scan",
        json={"site_id": site_id, "roi": SCAN_ROI, "start": "2021-07-01", "end": "2021-08-01"},
    )
    assert resp.status_code == 200
    job = _wait_succeeded(client, resp.json()["job_id"])
    det_ids = job["result"]["detection_ids"]
    assert len(det_ids) == 2  # both fake scenes produce a candidate

    # feed: ml-sourced rows with a score column
    feed = client.get("/api/methane/detections", params={"source": "ml"}).json()
    assert len(feed) == 2
    assert feed[0]["source"] == "ml"
    assert feed[0]["score"] is not None
    assert feed[0]["score"] > 0.5

    # detail: parsed result carries model_version, disagreement, review caption
    detail = client.get(f"/api/methane/detections/{det_ids[0]}").json()
    result = detail["result"]
    assert result["model_version"] == "plume_unet_v1"
    assert result["disagreement"] == "ml_only"  # no physics row for this site+scene
    assert result["review"].startswith("ML candidate")
    assert result["n_candidates"] >= 1
    # grid corners must be present so the detail overlay places on the map
    assert detail["overlay_bounds"] is not None
    assert len(detail["overlay_bounds"]) == 4

    # the ML overlay works because the npz carries xch4_ppb
    png = client.get(f"/api/methane/detections/{det_ids[0]}/overlay.png", params={"vmax": 200})
    assert png.status_code == 200
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
