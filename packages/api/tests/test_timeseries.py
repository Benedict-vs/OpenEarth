"""POST /timeseries + result download, with the core engine faked by name.

The engine seam (``services.timeseries.daily_timeseries``) is monkeypatched
to a canned frame that fires ``on_chunk`` twice; no Earth Engine is touched.
Job completion is observed by polling ``GET /jobs/{id}`` (the manager runs
the job on the TestClient's event loop).
"""

from __future__ import annotations

import threading
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from openearth.settings import Settings
from openearth_api.app import create_app
from openearth_api.deps import ensure_ee
from openearth_api.services import timeseries as ts_service

if TYPE_CHECKING:
    from fastapi import FastAPI

HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}
DATES = {"start": "2024-06-01", "end": "2024-07-01"}
NDVI_BODY = {"dataset": "s2", "product": "NDVI", "roi": HEIDELBERG, "dates": DATES}

# Canned daily frame the fake engine returns (two days across two chunks).
_FRAME = pd.DataFrame(
    {"value": [0.5, 1.0], "count": [100, 200]},
    index=pd.DatetimeIndex(["2024-06-05", "2024-06-20"], name="date"),
)


@pytest.fixture
def engine(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the core engine; ``gate`` (set by default) makes it instant."""
    app.dependency_overrides[ensure_ee] = lambda: None
    state: dict[str, Any] = {"calls": 0, "gate": threading.Event(), "scale_m": None}
    state["gate"].set()

    def fake_daily(
        data_key: str,
        source: str,
        roi: Any,
        start: Any,
        end: Any,
        *,
        scale_m: int | None = None,
        on_chunk: Any = None,
        cancel: Any = None,
    ) -> pd.DataFrame:
        state["calls"] += 1
        state["scale_m"] = scale_m
        state["gate"].wait(5.0)  # instant unless a test clears the gate
        if on_chunk is not None:
            on_chunk(1, 2, _FRAME.iloc[:1])
            on_chunk(2, 2, _FRAME.iloc[1:])
        return _FRAME

    monkeypatch.setattr(ts_service, "daily_timeseries", fake_daily)
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


# ── submit + result formats ──────────────────────────────────


def test_submit_runs_and_serves_all_formats(client: TestClient, engine: dict[str, Any]) -> None:
    response = client.post("/api/timeseries", json=NDVI_BODY)
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    _wait_status(client, job_id, "succeeded")

    # Native scale: S2 default_scale_m is 100.
    assert engine["scale_m"] == 100

    body = client.get(f"/api/timeseries/{job_id}/result?format=json").json()
    assert body["unit"] == "index"
    assert body["band"] == "NDVI"
    assert body["scale_m"] == 100
    assert body["display_scale"] == 1.0
    assert body["points"] == [
        {"date": "2024-06-05", "value": 0.5, "count": 100},
        {"date": "2024-06-20", "value": 1.0, "count": 200},
    ]

    csv = client.get(f"/api/timeseries/{job_id}/result?format=csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    assert csv.text.splitlines()[:2] == ["# unit: index", "date,value,count"]
    assert "2024-06-05,0.5,100" in csv.text

    parquet = client.get(f"/api/timeseries/{job_id}/result?format=parquet")
    assert parquet.status_code == 200
    assert parquet.headers["content-type"] == "application/vnd.apache.parquet"
    round_trip = pd.read_parquet(BytesIO(parquet.content))
    assert round_trip["value"].tolist() == [0.5, 1.0]
    assert round_trip["count"].tolist() == [100, 200]


def test_coarse_scale_multiplies_native(client: TestClient, engine: dict[str, Any]) -> None:
    response = client.post("/api/timeseries", json={**NDVI_BODY, "scale": "coarse"})
    job_id = response.json()["job_id"]
    _wait_status(client, job_id, "succeeded")
    assert engine["scale_m"] == 400  # 4 × 100


def test_identical_request_hits_cache(client: TestClient, engine: dict[str, Any]) -> None:
    first = client.post("/api/timeseries", json=NDVI_BODY).json()["job_id"]
    _wait_status(client, first, "succeeded")
    second = client.post("/api/timeseries", json=NDVI_BODY).json()["job_id"]
    _wait_status(client, second, "succeeded")

    # The engine computed once; the second job replayed from the cache.
    assert engine["calls"] == 1
    a = client.get(f"/api/timeseries/{first}/result").json()
    b = client.get(f"/api/timeseries/{second}/result").json()
    assert a["points"] == b["points"]


def test_coarse_and_native_are_distinct_cache_entries(
    client: TestClient, engine: dict[str, Any]
) -> None:
    native = client.post("/api/timeseries", json=NDVI_BODY).json()["job_id"]
    _wait_status(client, native, "succeeded")
    coarse = client.post("/api/timeseries", json={**NDVI_BODY, "scale": "coarse"}).json()["job_id"]
    _wait_status(client, coarse, "succeeded")
    assert engine["calls"] == 2  # different scale_m → different key → recompute


# ── validation / error semantics ─────────────────────────────


def test_rgb_product_is_422(client: TestClient, engine: dict[str, Any]) -> None:
    response = client.post(
        "/api/timeseries",
        json={"dataset": "s2", "product": "RGB", "roi": HEIDELBERG, "dates": DATES},
    )
    assert response.status_code == 422
    assert engine["calls"] == 0


def test_missing_roi_is_422(client: TestClient, engine: dict[str, Any]) -> None:
    response = client.post(
        "/api/timeseries", json={"dataset": "s2", "product": "NDVI", "dates": DATES}
    )
    assert response.status_code == 422  # roi is required


def test_unknown_job_result_is_404(client: TestClient) -> None:
    assert client.get("/api/timeseries/nope/result").status_code == 404


def test_result_before_done_is_409(client: TestClient, engine: dict[str, Any]) -> None:
    engine["gate"].clear()  # hold the runner inside the engine
    job_id = client.post("/api/timeseries", json=NDVI_BODY).json()["job_id"]
    try:
        response = client.get(f"/api/timeseries/{job_id}/result")
        assert response.status_code == 409
    finally:
        engine["gate"].set()
    _wait_status(client, job_id, "succeeded")


# ── SSE: progress + points + done ────────────────────────────


def _parse_sse(line: str, event: list[str | None], out: list[tuple[str, Any]]) -> None:
    import json

    if line.startswith(":"):
        return
    if line.startswith("event:"):
        event[0] = line[len("event:") :].strip()
    elif line.startswith("data:"):
        out.append((event[0] or "message", json.loads(line[len("data:") :].strip())))
        event[0] = None


@pytest.fixture
def sse_app(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = create_app(settings=test_settings)
    app.dependency_overrides[ensure_ee] = lambda: None
    gate = threading.Event()

    def fake_daily(*args: Any, on_chunk: Any = None, **kwargs: Any) -> pd.DataFrame:
        gate.wait(5.0)  # hold until the stream attaches
        if on_chunk is not None:
            on_chunk(1, 2, _FRAME.iloc[:1])
            on_chunk(2, 2, _FRAME.iloc[1:])
        return _FRAME

    monkeypatch.setattr(ts_service, "daily_timeseries", fake_daily)
    app.state.sse_gate = gate  # hand the gate to the test
    return app


@pytest.mark.asyncio
async def test_sse_progress_points_done(sse_app: FastAPI) -> None:
    import asyncio

    gate: threading.Event = sse_app.state.sse_gate

    async def run() -> list[tuple[str, Any]]:
        async with sse_app.router.lifespan_context(sse_app):
            transport = httpx.ASGITransport(app=sse_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                job_id = (await http.post("/api/timeseries", json=NDVI_BODY)).json()["job_id"]
                events: list[tuple[str, Any]] = []
                current: list[str | None] = [None]
                async with http.stream("GET", f"/api/jobs/{job_id}/events") as response:
                    async for line in response.aiter_lines():
                        before = len(events)
                        _parse_sse(line.rstrip("\r"), current, events)
                        if len(events) == before:
                            continue
                        gate.set()  # released once the stream is live
                        if events[-1][0] in ("done", "error") or len(events) > 30:
                            break
                return events

    events = await asyncio.wait_for(run(), timeout=10)
    names = [name for name, _ in events]
    assert "progress" in names
    assert "points" in names
    assert names[-1] == "done"
    assert names.index("progress") < names.index("points") < names.index("done")
    assert events[-1][1]["result"]["cache_key"]
