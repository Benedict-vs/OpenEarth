"""Stage 8 — methane sites/scenes/analyze/detections API (EE faked by name)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openearth_api.db import _MIGRATIONS, create_db_engine, migrate
from openearth_api.deps import ensure_ee
from openearth_api.services import methane as svc

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

KORPEZHE = {"kind": "bbox", "west": 53.7, "south": 38.2, "east": 54.7, "north": 38.8}
# A chip-sized sub-area of the site — the full site bbox exceeds the 20 m limit.
KORPEZHE_SUB = {"kind": "bbox", "west": 53.9, "south": 38.4, "east": 54.0, "north": 38.5}


def _wait_status(client: TestClient, job_id: str, status: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] == status:
            return
        if body["status"] in {"failed", "cancelled", "interrupted"} and status == "succeeded":
            raise AssertionError(f"job ended {body['status']}: {body.get('error')}")
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {status}")


# ── migration 3 ──


def _table_names(engine: Any) -> set[str]:
    from sqlalchemy import inspect as sa_inspect

    return set(sa_inspect(engine).get_table_names())


def test_migration3_on_fresh_db(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "fresh.db")
    assert migrate(engine) == len(_MIGRATIONS)
    assert {"sites", "detections", "reference_events"} <= _table_names(engine)


def test_migration3_on_v2_db(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "v2.db")
    # Bring the DB up to exactly version 2, then let migrate() apply the rest.
    with engine.begin() as conn:
        for batch in _MIGRATIONS[:2]:
            for stmt in batch:
                conn.exec_driver_sql(stmt)
        conn.exec_driver_sql("PRAGMA user_version = 2")
    assert migrate(engine) == len(_MIGRATIONS)
    assert {"sites", "detections", "reference_events"} <= _table_names(engine)


# ── seeding ──


def test_sites_seeded_once(client: TestClient) -> None:
    sites = client.get("/api/methane/sites").json()
    assert len(sites) == 7
    assert all(not s["name"].startswith("CH4: ") for s in sites)


def test_seed_sites_idempotent(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "seed.db")
    migrate(engine)
    svc.seed_sites(engine)
    svc.seed_sites(engine)  # second boot must not duplicate
    assert len(svc.list_sites(engine)) == 7


# ── sites CRUD + 409 ──


def test_sites_crud_and_duplicate_conflict(client: TestClient) -> None:
    body = {"name": "Test Site", "bbox": KORPEZHE, "notes": "hi"}
    created = client.post("/api/methane/sites", json=body)
    assert created.status_code == 201
    site_id = created.json()["id"]

    dup = client.post("/api/methane/sites", json=body)
    assert dup.status_code == 409

    patched = client.patch(f"/api/methane/sites/{site_id}", json={"notes": "updated"})
    assert patched.status_code == 200
    assert patched.json()["notes"] == "updated"

    assert client.delete(f"/api/methane/sites/{site_id}").status_code == 204
    assert client.patch(f"/api/methane/sites/{site_id}", json={"notes": "x"}).status_code == 404


# ── scenes route ──


def _fake_scene(scene_id: str, cloud: float) -> Any:
    from openearth.methane.scenes import S2Scene

    return S2Scene(
        scene_id=scene_id,
        time=datetime(2018, 6, 19, 7, 46, tzinfo=UTC),
        cloud_pct=cloud,
        relative_orbit=50,
        spacecraft="Sentinel-2A",
        sun_zenith_deg=40.0,
        view_zenith_deg=5.0,
    )


def test_scenes_route(client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    monkeypatch.setattr(
        svc,
        "list_scenes",
        lambda *a, **k: [_fake_scene("s_clear", 5.0), _fake_scene("s_cloud", 60.0)],
    )
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    resp = client.get(
        f"/api/methane/sites/{site_id}/scenes",
        params={"start": "2018-06-01", "end": "2018-07-01"},
    )
    assert resp.status_code == 200
    scenes = resp.json()
    assert [s["scene_id"] for s in scenes] == ["s_clear", "s_cloud"]
    assert scenes[0]["ref_ok"] is True  # cloud 5 ≤ 30
    assert scenes[1]["ref_ok"] is False  # cloud 60
    # amf = 1/cos(40°) + 1/cos(5°)
    assert scenes[0]["amf"] == pytest.approx(2.3092, abs=1e-3)


# ── analyze flow (canned DetectionResult) ──


def _canned_result(with_plume: bool = True) -> Any:
    from openearth.ee.pixels import GridSpec
    from openearth.methane.detect import DetectionResult
    from openearth.methane.ime import EmissionEstimate
    from openearth.methane.plume import PlumeMask
    from openearth.methane.wind import WindSample

    shape = (12, 12)
    grid = GridSpec(x0=53.9, y0=38.5, xscale=1e-4, yscale=1e-4, width=12, height=12)
    mask = np.zeros(shape, dtype=bool)
    if with_plume:
        mask[4:8, 4:8] = True
    xch4 = np.zeros(shape, dtype=np.float64)
    xch4[mask] = 120.0
    plume = PlumeMask(
        mask=mask, sigma=1.0, k_sigma=2.0, n_pixels=int(mask.sum()), area_m2=float(mask.sum() * 400)
    )
    emission = EmissionEstimate(
        q_kg_h=8000.0 if with_plume else float("nan"),
        q_sigma_kg_h=2000.0 if with_plume else float("nan"),
        percentiles={"p05": 5000.0, "p25": 6500.0, "p50": 8000.0, "p75": 9500.0, "p95": 11000.0},
        histogram={"edges": [0.0, 1.0], "counts": [1.0]},
        ime_kg=1000.0,
        l_m=80.0,
        u_eff_ms=1.77,
        u10_ms=4.0,
        sigma_u10_ms=1.5,
        wind_from_deg=270.0,
        n_mc=500,
    )
    scene = _fake_scene("20180619T074619_x", 5.0)
    return DetectionResult(
        target=scene,
        reference=None,
        method="mbsp",
        grid=grid,
        delta_r=np.zeros(shape),
        delta_omega=np.zeros(shape),
        xch4_ppb=xch4,
        rgb=np.full((*shape, 3), 0.2, dtype=np.float32),
        plume=plume,
        emission=emission,
        wind=WindSample.from_uv(scene.time, 4.0, 0.0, "test"),
        calibration={"c_target": 1.02, "c_ref": float("nan"), "n_excluded_target": 3.0},
        flags=[] if with_plume else ["no_plume"],
    )


@pytest.fixture
def analyze_ready(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    monkeypatch.setattr(svc, "analyze", lambda *a, **k: _canned_result(with_plume=True))


def test_analyze_flow_end_to_end(client: TestClient, analyze_ready: None) -> None:
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    resp = client.post(
        "/api/methane/analyze",
        json={
            "site_id": site_id,
            "roi": KORPEZHE_SUB,
            "target_scene_id": "20180619T074619_x",
            "method": "mbsp",
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    _wait_status(client, job_id, "succeeded")

    det_id = client.get(f"/api/jobs/{job_id}").json()["result"]["detection_id"]

    # Feed row — the roi analysis area must not break the site linkage
    feed = client.get("/api/methane/detections", params={"site_id": site_id}).json()
    assert len(feed) == 1
    assert feed[0]["id"] == det_id
    assert feed[0]["site_id"] == site_id
    assert feed[0]["q_kg_h"] == pytest.approx(8000.0)
    assert feed[0]["status"] == "candidate"

    # Detail
    detail = client.get(f"/api/methane/detections/{det_id}").json()
    assert detail["method"] == "mbsp"
    assert detail["ime_kg"] == pytest.approx(1000.0)
    assert detail["mask_geojson"]["type"] == "FeatureCollection"
    assert detail["overlay_bounds"][0] == [pytest.approx(53.9), pytest.approx(38.5)]
    assert detail["result"]["calibration"]["c_ref"] is None  # NaN → null

    # Overlay PNG honors vmin/vmax
    png = client.get(f"/api/methane/detections/{det_id}/overlay.png", params={"vmax": 200})
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Array download
    npz = client.get(f"/api/methane/detections/{det_id}/array.npz")
    assert npz.status_code == 200

    # PATCH status/notes
    patched = client.patch(
        f"/api/methane/detections/{det_id}", json={"status": "accepted", "notes": "looks real"}
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "accepted"
    assert patched.json()["notes"] == "looks real"

    # DELETE removes row + npz
    assert client.delete(f"/api/methane/detections/{det_id}").status_code == 204
    assert client.get(f"/api/methane/detections/{det_id}").status_code == 404


def test_analyze_requires_site_or_roi(client: TestClient, analyze_ready: None) -> None:
    resp = client.post("/api/methane/analyze", json={"target_scene_id": "x"})
    assert resp.status_code == 422  # neither site_id nor roi


def test_analyze_rejects_oversized_bbox_at_submit(client: TestClient, analyze_ready: None) -> None:
    # Seeded site ROIs are browse-scale (~100 km); without a chip-sized roi the
    # submit must 422 immediately instead of failing minutes into the job.
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    resp = client.post(
        "/api/methane/analyze",
        json={"site_id": site_id, "target_scene_id": "20180619T074619_x"},
    )
    assert resp.status_code == 422
    assert "Refusing" in resp.json()["detail"]


# ── tiles methane_ref quicklook ──


def test_tiles_methane_ref_unlocks_anomaly(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openearth_api.services import tiles as tiles_svc

    app.dependency_overrides[ensure_ee] = lambda: None
    called = {"anomaly": False}

    def fake_anomaly(*_a: Any, **_k: Any) -> Any:
        called["anomaly"] = True
        return object()  # a stand-in ee.Image; mint is faked below

    monkeypatch.setattr(tiles_svc, "build_methane_anomaly_composite", fake_anomaly)
    monkeypatch.setattr(
        tiles_svc,
        "mint_tile_url",
        lambda *a, **k: type(
            "R",
            (),
            {
                "url": "https://x/{z}/{x}/{y}",
                "expires_at": datetime(2030, 1, 1, tzinfo=UTC),
                "attribution": "ESA",
            },
        )(),
    )

    body = {
        "dataset": "s2",
        "product": "CH4_ANOMALY",
        "roi": KORPEZHE,
        "composite": "date_window",
        "target_date": "2018-06-19",
        "methane_ref": {"start": "2018-05-01", "end": "2018-06-01"},
    }
    resp = client.post("/api/tiles", json=body)
    assert resp.status_code == 200, resp.text
    assert called["anomaly"] is True

    # Without methane_ref it still 422s.
    del body["methane_ref"]
    assert client.post("/api/tiles", json=body).status_code == 422


# ── Screening job (Stage 9) ──


def test_screening_job(client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    from openearth.methane.tropomi import Hotspot
    from openearth_api.services import methane as svc_mod

    app.dependency_overrides[ensure_ee] = lambda: None
    monkeypatch.setattr(
        svc_mod,
        "screen_region",
        lambda *a, **k: [Hotspot(38.5, 53.9, 40.0, 55.0, 4.2, 3, 3)],
    )
    resp = client.post(
        "/api/methane/screening",
        json={"roi": KORPEZHE, "start": "2023-06-01", "end": "2023-07-01"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    _wait_status(client, job_id, "succeeded")
    result = client.get(f"/api/jobs/{job_id}").json()["result"]
    assert result["hotspots"][0]["mean_enh_ppb"] == pytest.approx(40.0)
    assert result["hotspots"][0]["weeks_flagged"] == 3


# ── Validation (Stage 9) ──


def test_validation_import_and_cross_match(client: TestClient, analyze_ready: None) -> None:
    # Import a small CSV of reference events (one near the Korpezhe detection).
    csv = (
        b"lat,lon,date,rate\n"
        b"38.5,53.9,2018-06-19,11.2\n"  # coincident in space+time
        b"0.0,0.0,2000-01-01,1.0\n"  # far away
    )
    imp = client.post(
        "/api/methane/validation/import",
        files={"file": ("events.csv", csv, "text/csv")},
        data={"source": "imeo", "fmt": "csv"},
    )
    assert imp.status_code == 200
    assert imp.json() == {"imported": 2, "skipped": 0}

    events = client.get("/api/methane/validation/events").json()
    assert len(events) == 2

    # Run an analysis to get a detection near (38.5, 53.9), then validate it.
    site_id = client.get("/api/methane/sites").json()[0]["id"]
    job = client.post(
        "/api/methane/analyze",
        json={
            "site_id": site_id,
            "roi": KORPEZHE_SUB,
            "target_scene_id": "20180619T074619_x",
            "method": "mbsp",
        },
    ).json()
    _wait_status(client, job["job_id"], "succeeded")
    det_id = client.get(f"/api/jobs/{job['job_id']}").json()["result"]["detection_id"]

    verdict = client.post(f"/api/methane/detections/{det_id}/validate").json()
    assert verdict["verdict"] == "confirmed"
    assert len(verdict["matched_event_ids"]) == 1

    # The verdict is persisted on the detection detail.
    detail = client.get(f"/api/methane/detections/{det_id}").json()
    assert detail["validation"]["verdict"] == "confirmed"


def test_validation_import_counts_skipped(client: TestClient) -> None:
    csv = b"lat,lon,date,rate\n38.5,53.9,2018-06-19,10\n,53.9,2018-06-19,10\n"
    imp = client.post(
        "/api/methane/validation/import",
        files={"file": ("e.csv", csv, "text/csv")},
        data={"source": "manual", "fmt": "csv"},
    )
    assert imp.json() == {"imported": 1, "skipped": 1}
