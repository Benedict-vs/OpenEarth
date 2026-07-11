"""Phase 6 — EMIT plume routes + earthaccess V002 fallback + cross-match (EE/earthaccess faked)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from openearth.methane.emit import EmitPlume
from openearth_api.db import _MIGRATIONS, create_db_engine, migrate
from openearth_api.deps import ensure_ee
from openearth_api.models import Detection, utcnow_iso
from openearth_api.services import emit as svc_emit
from openearth_api.services import methane as svc

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

# A schema-faithful single-feature V002 granule (what a CH4PLMMETA .json holds).
_V002_GRANULE = json.dumps(
    {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "Plume ID": "CH4_PlumeComplex-9001",
                    "DAAC Scene Names": ["EMIT_L2B_CH4ENH_002_20250115T190000_2501512_005"],
                    "UTC Time Observed": "2025-01-15T19:00:00Z",
                    "Max Plume Concentration (ppm m)": 2100.0,
                    "Latitude of max concentration": 32.4,
                    "Longitude of max concentration": -102.1,
                    "Emissions Rate Estimate (kg/hr)": 1450.0,
                    "Emissions Rate Estimate Uncertainty (kg/hr)": 480.0,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-102.11, 32.39],
                            [-102.09, 32.39],
                            [-102.09, 32.41],
                            [-102.11, 32.41],
                            [-102.11, 32.39],
                        ]
                    ],
                },
            }
        ],
    }
).encode()


def _gee_plume(lat: float, lon: float, time: str) -> EmitPlume:
    d = 0.003
    ring = [
        [lon - d, lat - d],
        [lon + d, lat - d],
        [lon + d, lat + d],
        [lon - d, lat + d],
        [lon - d, lat - d],
    ]
    return EmitPlume(
        plume_id="EMIT_L2B_CH4PLM_001_x",
        outline={"type": "Polygon", "coordinates": [ring]},
        time_utc=datetime.fromisoformat(time.replace("Z", "+00:00")),
        max_enh_ppm_m=3100.0,
        max_enh_lat=lat,
        max_enh_lon=lon,
        q_kg_h=None,
        q_sigma_kg_h=None,
        provenance="gee_v001",
        source_scenes=[],
    )


class _FakeGranule:
    def data_links(self, access: str = "out_of_region") -> list[str]:
        # The COG must be skipped; only the CH4PLMMETA .json is fetched.
        return [
            "https://data.lpdaac.earthdatacloud.nasa.gov/x/EMIT_L2B_CH4PLM_002_20250115T190000_2501512.tif",
            "https://data.lpdaac.earthdatacloud.nasa.gov/x/EMIT_L2B_CH4PLMMETA_002_20250115T190000_2501512.json",
        ]


class _FakeSession:
    def __init__(self, fetched: list[str]) -> None:
        self._fetched = fetched

    def get(self, url: str, timeout: int = 60) -> Any:
        self._fetched.append(url)
        return SimpleNamespace(content=_V002_GRANULE, raise_for_status=lambda: None)


def _fake_earthaccess(*, login_ok: bool, fetched: list[str]) -> Any:
    def login(strategy: str = "environment") -> Any:
        if not login_ok:
            raise RuntimeError("no Earthdata credentials")
        return object()

    return SimpleNamespace(
        login=login,
        search_data=lambda **kw: [_FakeGranule()],
        get_requests_https_session=lambda: _FakeSession(fetched),
    )


# ── migration 5 (emit_json column, additive, null = never checked) ──


def _columns(engine: Any, table: str) -> set[str]:
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
    return {r[1] for r in rows}


def test_migration5_on_v4_db_adds_emit_json_nullable(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "v4.db")
    # Bring the DB to exactly version 4, insert a pre-migration detection row.
    with engine.begin() as conn:
        for batch in _MIGRATIONS[:4]:
            for stmt in batch:
                conn.exec_driver_sql(stmt)
        conn.exec_driver_sql("PRAGMA user_version = 4")
        conn.exec_driver_sql(
            "INSERT INTO detections "
            "(id, source, status, method, scene_id, scene_time_utc, params_json, "
            " result_json, array_path, created_at, updated_at) "
            "VALUES ('old', 'physics', 'candidate', 'mbmp', 'sc', '2023-01-01T00:00:00+00:00', "
            "'{}', '{}', 'detections/old.npz', '2023-01-01', '2023-01-01')"
        )
    assert "emit_json" not in _columns(engine, "detections")

    assert migrate(engine) == len(_MIGRATIONS)
    assert "emit_json" in _columns(engine, "detections")
    # The pre-existing row survives with emit_json IS NULL → "never checked".
    with engine.begin() as conn:
        value = conn.exec_driver_sql("SELECT emit_json FROM detections WHERE id='old'").scalar_one()
    assert value is None
    assert svc._emit_matches_of(value) is None


# ── /methane/emit/plumes ──


def test_emit_plumes_gee_path(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    monkeypatch.setattr(
        svc_emit,
        "list_plumes_gee",
        lambda *a, **k: [
            _gee_plume(32.8, -103.7, "2023-06-16T21:13:43Z"),
            _gee_plume(31.5, -101.9, "2023-06-16T21:14:19Z"),
        ],
    )
    resp = client.get(
        "/api/methane/emit/plumes",
        params={
            "west": -104,
            "south": 31,
            "east": -101,
            "north": 33.5,
            "start": "2023-06-10",
            "end": "2023-06-24",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provenance_paths"] == ["gee_v001"]  # window before the freeze → GEE only
    assert len(body["plumes"]) == 2
    assert body["plumes"][0]["provenance"] == "gee_v001"
    assert body["plumes"][0]["q_kg_h"] is None  # V001 carries no emission rate
    assert body["plumes"][0]["outline"]["type"] == "Polygon"


def test_emit_plumes_v002_path_via_earthaccess(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    fetched: list[str] = []
    fake = _fake_earthaccess(login_ok=True, fetched=fetched)
    monkeypatch.setitem(sys.modules, "earthaccess", fake)
    resp = client.get(
        "/api/methane/emit/plumes",
        params={
            "west": -103,
            "south": 31,
            "east": -101,
            "north": 33,
            "start": "2025-01-01",
            "end": "2025-02-01",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provenance_paths"] == ["lpdaac_v002"]  # window after the freeze → V002 only
    assert len(body["plumes"]) == 1
    plume = body["plumes"][0]
    assert plume["provenance"] == "lpdaac_v002"
    assert plume["plume_id"] == "CH4_PlumeComplex-9001"
    assert plume["q_kg_h"] == pytest.approx(1450.0)  # V002 emission rate parsed
    assert plume["source_scenes"] == ["EMIT_L2B_CH4ENH_002_20250115T190000_2501512_005"]
    # Only the CH4PLMMETA .json was fetched — the COG was skipped.
    assert len(fetched) == 1
    assert "CH4PLMMETA" in fetched[0]


def test_emit_plumes_v002_missing_credentials_502(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_earthaccess(login_ok=False, fetched=[]))
    resp = client.get(
        "/api/methane/emit/plumes",
        params={
            "west": -103,
            "south": 31,
            "east": -101,
            "north": 33,
            "start": "2025-01-01",
            "end": "2025-02-01",
        },
    )
    assert resp.status_code == 502
    assert "EARTHDATA_TOKEN" in resp.json()["detail"]


# ── /methane/detections/{id}/emit-match ──

# Grid corners [[w,n],[e,n],[e,s],[w,s]] → detection center ≈ (38.4994, 53.9006).
_DET_BOUNDS = [[53.9, 38.5], [53.9012, 38.5], [53.9012, 38.4988], [53.9, 38.4988]]
_DET_CENTER = (38.4994, 53.9006)


def _insert_detection(app: FastAPI, det_id: str = "det1") -> str:
    now = utcnow_iso()
    row = Detection(
        id=det_id,
        site_id=None,
        source="physics",
        status="candidate",
        method="mbmp",
        scene_id="20180619T074619_x",
        scene_time_utc="2018-06-19T07:46:00+00:00",
        params_json="{}",
        result_json=json.dumps({"flags": [], "overlay_bounds": _DET_BOUNDS}),
        array_path=f"detections/{det_id}.npz",
        created_at=now,
        updated_at=now,
    )
    with Session(app.state.db_engine) as session:
        session.add(row)
        session.commit()
    return det_id


def test_emit_match_writes_emit_json(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    det_id = _insert_detection(app)

    # Feed shows "never checked" before a match runs.
    feed = client.get("/api/methane/detections").json()
    assert feed[0]["emit_matches"] is None

    lat, lon = _DET_CENTER
    monkeypatch.setattr(
        svc_emit,
        "list_plumes_gee",
        lambda *a, **k: [_gee_plume(lat, lon, "2018-06-19T07:46:00Z")],
    )
    resp = client.post(f"/api/methane/detections/{det_id}/emit-match")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["emit_json"] is not None
    assert detail["emit_json"]["provenance_paths"] == ["gee_v001"]
    matches = detail["emit_json"]["matches"]
    assert len(matches) == 1
    assert matches[0]["distance_km"] == pytest.approx(0.0, abs=0.1)
    assert matches[0]["plume"]["provenance"] == "gee_v001"

    # Feed chip now reflects the match count.
    feed = client.get("/api/methane/detections").json()
    assert feed[0]["emit_matches"] == 1


def test_emit_match_no_plumes_records_zero(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[ensure_ee] = lambda: None
    det_id = _insert_detection(app)
    monkeypatch.setattr(svc_emit, "list_plumes_gee", lambda *a, **k: [])
    resp = client.post(f"/api/methane/detections/{det_id}/emit-match")
    assert resp.status_code == 200
    assert resp.json()["emit_json"]["matches"] == []
    # Checked-with-no-match (0) is distinct from never-checked (None).
    feed = client.get("/api/methane/detections").json()
    assert feed[0]["emit_matches"] == 0
