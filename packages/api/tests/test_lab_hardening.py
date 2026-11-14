"""Methane Lab hardening: bad input is a 422 (never a 500/502), floors survive
renames, a human verdict survives re-validation, coincidence matches measure
from the plume, and a finished job reads back its final progress."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from openearth.ee.pixels import grid_for
from openearth.geometry import BBox
from openearth.methane.emit import EmitPlume
from openearth.methane.plume import mask_outline_geojson
from openearth_api.deps import ensure_ee
from openearth_api.models import Detection, utcnow_iso
from openearth_api.services import emit as svc_emit
from openearth_api.services import methane as svc

if TYPE_CHECKING:
    from fastapi import FastAPI

ROI = {"kind": "bbox", "west": 53.9, "south": 38.4, "east": 54.0, "north": 38.5}


@pytest.fixture
def ee_free(app: FastAPI) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None


def _site_id(client: TestClient) -> int:
    return int(client.get("/api/methane/sites").json()[0]["id"])


# ── 422, not 500/502 ──


@pytest.mark.parametrize(
    "query",
    [
        "start=garbage&end=2018-07-01",  # was a 502 "Unexpected Earth Engine error"
        "start=2018-07-01&end=2018-06-01",  # reversed: was a 502
        "start=2018-06-01&end=2018-06-01",  # empty [start, end)
    ],
)
def test_scenes_route_rejects_bad_dates(client: TestClient, ee_free: None, query: str) -> None:
    assert client.get(f"/api/methane/sites/{_site_id(client)}/scenes?{query}").status_code == 422


def test_emit_route_rejects_bad_dates(client: TestClient, ee_free: None) -> None:
    box = "west=-103.85&south=32.75&east=-103.6&north=32.92"
    assert (
        client.get(f"/api/methane/emit/plumes?{box}&start=garbage&end=2023-06-01").status_code
        == 422
    )
    reversed_ = client.get(f"/api/methane/emit/plumes?{box}&start=2023-06-02&end=2023-06-01")
    assert reversed_.status_code == 422


def test_nan_in_a_body_is_a_422_not_a_500(client: TestClient, ee_free: None) -> None:
    body = '{"site_id": 1, "roi": ' + json.dumps(ROI) + ', "target_scene_id": "x", "k_sigma": NaN}'
    resp = client.post(
        "/api/methane/analyze", content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422
    assert "k_sigma" in json.dumps(resp.json())


@pytest.mark.parametrize(
    "extra",
    [
        {"seed": -1},  # default_rng(-1) used to raise after all the EE work
        {"reference_scene_id": "SAME"},  # ΔΩ ≡ 0 — a fake "no plume"
        {"source_lonlat": [200.0, 10.0]},
    ],
)
def test_analyze_request_rejections(
    client: TestClient, ee_free: None, extra: dict[str, Any]
) -> None:
    body = {"site_id": _site_id(client), "roi": ROI, "target_scene_id": "SAME", **extra}
    assert client.post("/api/methane/analyze", json=body).status_code == 422


def test_reversed_windows_rejected(client: TestClient, ee_free: None) -> None:
    sid = _site_id(client)
    scan = {"site_id": sid, "roi": ROI, "start": "2021-08-01", "end": "2021-07-01"}
    assert client.post("/api/methane/ml/scan", json=scan).status_code == 422
    screen = {"roi": ROI, "start": "2023-09-01", "end": "2023-06-01"}
    assert client.post("/api/methane/screening", json=screen).status_code == 422


def test_screening_size_caps(client: TestClient, ee_free: None) -> None:
    long = {"roi": ROI, "start": "2019-03-01", "end": "2023-03-01"}
    assert client.post("/api/methane/screening", json=long).status_code == 422
    globe = {"kind": "bbox", "west": -179, "south": -80, "east": 179, "north": 80}
    huge = {"roi": globe, "start": "2023-06-01", "end": "2023-07-01", "cell_deg": 0.05}
    resp = client.post("/api/methane/screening", json=huge)
    assert resp.status_code == 422
    assert "cell" in resp.json()["detail"]


def test_site_date_hint_order(client: TestClient) -> None:
    bad = {
        "name": "zz-hint",
        "bbox": ROI,
        "date_hint_start": "2024-02-01",
        "date_hint_end": "2024-01-01",
    }
    assert client.post("/api/methane/sites", json=bad).status_code == 422
    ok = client.post("/api/methane/sites", json={**bad, "date_hint_end": "2024-03-01"}).json()
    one_sided = client.patch(f"/api/methane/sites/{ok['id']}", json={"date_hint_end": "2024-01-01"})
    assert one_sided.status_code == 422


def test_overlay_range_rejected(client: TestClient) -> None:
    for query in ("vmin=200&vmax=100", "vmin=nan", "vmax=inf"):
        assert client.get(f"/api/methane/detections/x/overlay.png?{query}").status_code == 422


# ── noise floor: 404 for ghost sites, keyed rename-proof ──


def test_noise_floor_404_and_survives_rename(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/methane/sites/99999/noise-floor").status_code == 404

    site = client.get("/api/methane/sites").json()[0]
    floor = {"sites": {site["name"]: {"floor_kg_h": 6300.0}}, "global": {"floor_kg_h": 5000.0}}
    monkeypatch.setattr(svc, "load_floor", lambda: floor)
    client.patch(f"/api/methane/sites/{site['id']}", json={"name": "renamed by user"})
    body = client.get(f"/api/methane/sites/{site['id']}/noise-floor").json()
    assert (body["floor_kg_h"], body["floor_source"]) == (6300.0, "site")


# ── human verdict ──


def _insert(
    app: FastAPI, *, det_id: str, mask_geojson: str | None, bounds: list[list[float]]
) -> None:
    now = utcnow_iso()
    row = Detection(
        id=det_id,
        site_id=None,
        source="physics",
        status="candidate",
        method="mbmp",
        scene_id="S",
        scene_time_utc="2023-06-16T07:00:00+00:00",
        params_json="{}",
        result_json=json.dumps({"overlay_bounds": bounds}),
        mask_geojson=mask_geojson,
        array_path="x.npz",
        created_at=now,
        updated_at=now,
    )
    with Session(app.state.db_engine) as session:
        session.add(row)
        session.commit()


_CHIP = BBox(53.9, 38.4, 54.1, 38.6)  # ~17 km wide
_CHIP_BOUNDS = [[53.9, 38.6], [54.1, 38.6], [54.1, 38.4], [53.9, 38.4]]


def test_human_contradicted_verdict_survives_revalidation(client: TestClient, app: FastAPI) -> None:
    _insert(app, det_id="v1", mask_geojson=None, bounds=_CHIP_BOUNDS)
    patched = client.patch("/api/methane/detections/v1", json={"verdict": "contradicted"}).json()
    assert patched["validation"]["verdict"] == "contradicted"

    revalidated = client.post("/api/methane/detections/v1/validate", json={}).json()
    assert revalidated["verdict"] == "contradicted"  # the automatic match no longer wins

    cleared = client.patch("/api/methane/detections/v1", json={"verdict": None}).json()
    assert cleared["validation"] is None


# ── coincidence matches measure from the plume, not the chip centre ──


def test_emit_match_uses_the_plume_centroid(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    grid = grid_for(_CHIP, 20)
    mask = np.zeros((grid.height, grid.width), dtype=bool)
    mask[5:15, 5:15] = True  # NW corner: ~10 km from the chip centre
    _insert(
        app,
        det_id="e1",
        mask_geojson=json.dumps(mask_outline_geojson(mask, grid)),
        bounds=_CHIP_BOUNDS,
    )
    plume_lon = grid.x0 + 10 * grid.xscale
    plume_lat = grid.y0 - 10 * grid.yscale
    plume = EmitPlume(
        plume_id="P",
        outline={"type": "Point", "coordinates": [plume_lon, plume_lat]},
        time_utc=datetime(2023, 6, 16, 8, tzinfo=UTC),
        max_enh_ppm_m=None,
        max_enh_lat=plume_lat,
        max_enh_lon=plume_lon,
        q_kg_h=None,
        q_sigma_kg_h=None,
        provenance="gee_v001",
        source_scenes=[],
    )
    monkeypatch.setattr(svc_emit, "list_plumes", lambda *a, **k: ([plume], ["gee_v001"]))
    app.dependency_overrides[ensure_ee] = lambda: None
    body = client.post("/api/methane/detections/e1/emit-match", json={}).json()
    assert len(body["emit_json"]["matches"]) == 1  # missed when measured from the chip centre


def test_emit_match_near_the_antimeridian(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(
        app,
        det_id="am",
        mask_geojson=None,
        bounds=[[179.8, 10.1], [180.0, 10.1], [180.0, 9.9], [179.8, 9.9]],
    )
    seen: list[BBox] = []
    monkeypatch.setattr(
        svc_emit, "list_plumes", lambda bbox, *a, **k: (seen.append(bbox) or [], [])
    )
    app.dependency_overrides[ensure_ee] = lambda: None
    assert client.post("/api/methane/detections/am/emit-match", json={}).status_code == 200
    assert seen[0].east == 180.0


# ── jobs read back their final progress ──


def test_finished_job_persists_final_progress(client: TestClient, app: FastAPI) -> None:
    # Progress writes are throttled (0.25 s); 7 instant steps used to leave the
    # row at an early step after the job had succeeded.
    def runner(ctx: Any) -> dict[str, Any]:
        for i in range(1, 8):
            ctx.progress(i, 7, f"step {i}")
        return {}

    job_id = client.portal.call(app.state.jobs.submit, "test_job", {}, runner)  # type: ignore[union-attr]
    deadline = time.monotonic() + 5
    while client.get(f"/api/jobs/{job_id}").json()["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    assert (body["progress_done"], body["progress_total"], body["message"]) == (7, 7, "step 7")
