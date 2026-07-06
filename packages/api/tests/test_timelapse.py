"""Stage 3 — timelapse migration 4, render job round-trip, gallery, artifacts.

Earth Engine and the heavy render/encode pipeline are faked at the service
module level; the fakes write real tiny files so the routes exercise the real
DB rows, manifest parse, and FileResponse plumbing.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openearth_api.db import _MIGRATIONS, create_db_engine, migrate
from openearth_api.deps import ensure_ee
from openearth_api.services import timelapse as svc

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from openearth.settings import Settings

ROI = {"kind": "bbox", "west": 8.5, "south": 49.3, "east": 8.8, "north": 49.5}


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


# ── migration 4 ──


def _table_names(engine: Any) -> set[str]:
    from sqlalchemy import inspect as sa_inspect

    return set(sa_inspect(engine).get_table_names())


def test_migration4_on_fresh_db(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "fresh.db")
    assert migrate(engine) == len(_MIGRATIONS)
    assert "renders" in _table_names(engine)


def test_migration4_on_v3_db(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "v3.db")
    with engine.begin() as conn:
        for batch in _MIGRATIONS[:3]:
            for stmt in batch:
                conn.exec_driver_sql(stmt)
        conn.exec_driver_sql("PRAGMA user_version = 3")
    assert migrate(engine) == len(_MIGRATIONS)
    assert "renders" in _table_names(engine)


def test_migration_idempotent(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "idem.db")
    assert migrate(engine) == len(_MIGRATIONS)
    assert migrate(engine) == len(_MIGRATIONS)  # second run is a no-op


# ── render job round-trip (fakes write real tiny files) ──


def _fake_render(out_dir: Path, vis: tuple[float, float]) -> SimpleNamespace:
    paths = []
    for i in range(2):
        p = out_dir / f"frame_{i:04d}.png"
        Image.new("RGB", (8, 6), (i * 20, 0, 0)).save(p, format="PNG")
        paths.append(p)
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "dataset": "s5p",
                "product": "NO2",
                "width": 8,
                "height": 6,
                "vis": list(vis),
                "frames": [
                    {
                        "index": 0,
                        "start": "2024-06-01",
                        "end": "2024-06-30",
                        "label": "2024-06",
                        "status": "rendered",
                    },
                    {
                        "index": 1,
                        "start": "2024-07-01",
                        "end": "2024-07-31",
                        "label": "2024-07",
                        "status": "rendered",
                    },
                ],
            }
        )
    )
    return SimpleNamespace(frame_paths=paths, rendered_count=2, width=8, height=6)


@pytest.fixture
def timelapse_ready(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None

    def fake_render_frames(dataset, product, roi, windows, *, out_dir, vis_min, vis_max, **kw):
        manifest = _fake_render(out_dir, (vis_min or 0.0, vis_max or 1.0))
        for i in range(2):  # drive the live-preview hooks like the real renderer
            if kw.get("on_frame") is not None:
                kw["on_frame"](i, "rendered", 2)
            if kw.get("on_progress") is not None:
                kw["on_progress"](i + 1, 2)
        return manifest

    def fake_encode_movie(frame_paths, out_path, *, fmt, fps):
        out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42FAKE")

    monkeypatch.setattr(svc, "render_frames", fake_render_frames)
    monkeypatch.setattr(svc, "encode_movie", fake_encode_movie)


def _submit(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dataset": "s5p",
        "product": "NO2",
        "roi": ROI,
        "dates": {"start": "2024-06-01", "end": "2024-07-31"},
        "step": {"mode": "monthly"},
        "format": "mp4",
        "fps": 6,
    }
    body.update(overrides)
    resp = client.post("/api/timelapse", json=body)
    return {"status": resp.status_code, "json": resp.json() if resp.content else None}


def test_timelapse_round_trip(
    client: TestClient, timelapse_ready: None, test_settings: Settings
) -> None:
    out = _submit(client)
    assert out["status"] == 200, out
    job_id = out["json"]["job_id"]
    render_id = out["json"]["render_id"]
    _wait_status(client, job_id, "succeeded")
    assert client.get(f"/api/jobs/{job_id}").json()["result"]["render_id"] == render_id

    # Gallery row is succeeded with the frame count.
    gallery = client.get("/api/timelapse").json()
    assert len(gallery) == 1
    assert gallery[0]["id"] == render_id
    assert gallery[0]["status"] == "succeeded"
    assert gallery[0]["frame_count"] == 2
    assert gallery[0]["movie_bytes"] > 0

    # Detail carries the parsed manifest and the ROI.
    detail = client.get(f"/api/timelapse/{render_id}").json()
    assert detail["manifest"]["frames"][1]["label"] == "2024-07"
    assert detail["roi"]["kind"] == "bbox"
    assert detail["params"]["dataset"] == "s5p"

    # Frame PNG is served.
    frame = client.get(f"/api/timelapse/{render_id}/frames/0")
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/png"
    assert frame.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/timelapse/{render_id}/frames/9").status_code == 404

    # Download serves the movie with a descriptive filename.
    dl = client.get(f"/api/timelapse/{render_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "video/mp4"
    assert "s5p_NO2_2024-06-01_2024-07-31.mp4" in dl.headers["content-disposition"]

    # Delete removes the row and the on-disk render directory.
    render_dir = test_settings.data_dir / "timelapse" / render_id
    assert render_dir.exists()
    assert client.delete(f"/api/timelapse/{render_id}").status_code == 204
    assert not render_dir.exists()
    assert client.get(f"/api/timelapse/{render_id}").status_code == 404


def test_delete_running_render_conflicts(
    client: TestClient, app: FastAPI, test_settings: Settings
) -> None:
    from sqlmodel import Session

    from openearth_api.models import Render, utcnow_iso

    engine = app.state.db_engine
    now = utcnow_iso()
    with Session(engine) as session:
        session.add(
            Render(
                id="run123",
                title="t",
                dataset="s5p",
                product="NO2",
                params_json="{}",
                roi_json=json.dumps(ROI),
                status="running",
                fps=6,
                format="mp4",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    assert client.delete("/api/timelapse/run123").status_code == 409


# ── validation (422 / 404) before any EE work ──


def test_builder_product_422(client: TestClient, timelapse_ready: None) -> None:
    out = _submit(client, dataset="s2", product="CH4_ANOMALY")
    assert out["status"] == 422


def test_unknown_product_404(client: TestClient, timelapse_ready: None) -> None:
    out = _submit(client, product="NOPE")
    assert out["status"] == 404


def test_malformed_roi_422(client: TestClient, timelapse_ready: None) -> None:
    bad = {"kind": "bbox", "west": 10.0, "south": 0.0, "east": 5.0, "north": 1.0}  # west > east
    out = _submit(client, roi=bad)
    assert out["status"] == 422


def test_single_frame_422(client: TestClient, timelapse_ready: None) -> None:
    # A few-day span with monthly stepping is a single frame → refused.
    out = _submit(
        client,
        dates={"start": "2024-06-01", "end": "2024-06-05"},
        step={"mode": "monthly"},
    )
    assert out["status"] == 422


def test_over_budget_422(client: TestClient, timelapse_ready: None) -> None:
    out = _submit(
        client,
        dates={"start": "2000-01-01", "end": "2010-01-01"},
        step={"mode": "interval", "interval_days": 1},
    )
    assert out["status"] == 422


def test_gif_frame_cap_422(client: TestClient, timelapse_ready: None) -> None:
    out = _submit(
        client,
        format="gif",
        dates={"start": "2024-01-01", "end": "2024-08-01"},  # ~213 daily frames
        step={"mode": "interval", "interval_days": 1, "window_days": 1},
    )
    assert out["status"] == 422
