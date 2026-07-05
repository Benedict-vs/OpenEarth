"""Job manager, interrupted-sweep, and the SSE event stream.

The manager unit tests exercise the concurrency invariants directly (no
HTTP); the sweep and SSE tests go through the app so the wiring is covered
end to end. No Earth Engine anywhere — runners are plain callables.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlmodel import Session

from openearth.errors import JobError
from openearth.settings import Settings
from openearth_api.app import create_app
from openearth_api.db import create_db_engine, migrate
from openearth_api.jobs import MAX_RUNNING_JOBS, JobContext, JobManager
from openearth_api.models import TERMINAL_STATUSES, Job, utcnow_iso

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest_asyncio.fixture
async def manager(tmp_path: Path) -> AsyncIterator[JobManager]:
    engine = create_db_engine(tmp_path / "openearth.db")
    migrate(engine)
    mgr = JobManager(engine)
    mgr.start()
    try:
        yield mgr
    finally:
        await mgr.stop()
        engine.dispose()


async def _wait_terminal(mgr: JobManager, job_id: str, timeout: float = 5.0) -> Job:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = mgr.get(job_id)
        if row is not None and row.status in TERMINAL_STATUSES:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not terminate within {timeout}s")


async def _collect(gen: AsyncIterator[tuple[str, dict[str, Any]]]) -> list[tuple[str, Any]]:
    return [item async for item in gen]


# ── manager unit tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_persists_result(manager: JobManager) -> None:
    def runner(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(1, 1, "only chunk")
        return {"cache_key": "abc"}

    job_id = await manager.submit("timeseries", {"x": 1}, runner)
    row = await _wait_terminal(manager, job_id)

    assert row.status == "succeeded"
    assert row.result_json is not None
    assert json.loads(row.result_json) == {"cache_key": "abc"}
    assert row.error is None
    assert row.started_at is not None
    assert row.finished_at is not None
    assert json.loads(row.params_json) == {"x": 1}


@pytest.mark.asyncio
async def test_job_error_marks_failed(manager: JobManager) -> None:
    def runner(ctx: JobContext) -> dict[str, Any]:
        raise JobError("boom")

    job_id = await manager.submit("timeseries", {}, runner)
    row = await _wait_terminal(manager, job_id)

    assert row.status == "failed"
    assert row.error == "boom"
    assert row.result_json is None


@pytest.mark.asyncio
async def test_unexpected_exception_marks_failed_with_message(manager: JobManager) -> None:
    def runner(ctx: JobContext) -> dict[str, Any]:
        raise ValueError("kaboom")

    job_id = await manager.submit("timeseries", {}, runner)
    row = await _wait_terminal(manager, job_id)

    assert row.status == "failed"
    assert row.error is not None
    assert "kaboom" in row.error


@pytest.mark.asyncio
async def test_cancel_mid_run(manager: JobManager) -> None:
    started = threading.Event()

    def runner(ctx: JobContext) -> dict[str, Any]:
        started.set()
        # Poll the cancel flag at "chunk boundaries" until asked to stop.
        while not ctx.cancelled.wait(timeout=0.02):
            pass
        raise JobError("stopped early")  # a non-"cancelled" message on purpose

    job_id = await manager.submit("timeseries", {}, runner)
    await asyncio.to_thread(started.wait, 2.0)
    await manager.cancel(job_id)
    row = await _wait_terminal(manager, job_id)

    # A raise after the flag is set is a cancellation regardless of the
    # exception message — status wins over the runner's error text.
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_while_queued_never_runs(manager: JobManager) -> None:
    release = threading.Event()

    def blocker(ctx: JobContext) -> dict[str, Any]:
        release.wait(2.0)
        return {}

    # Saturate every running slot so the next submission stays queued.
    for _ in range(MAX_RUNNING_JOBS):
        await manager.submit("timeseries", {}, blocker)

    def should_not_run(ctx: JobContext) -> dict[str, Any]:
        return {"ran": True}

    queued_id = await manager.submit("timeseries", {}, should_not_run)
    await asyncio.sleep(0.1)
    assert manager.get(queued_id).status == "queued"  # type: ignore[union-attr]

    await manager.cancel(queued_id)
    release.set()
    row = await _wait_terminal(manager, queued_id)

    assert row.status == "cancelled"
    assert row.result_json is None  # the runner never executed


@pytest.mark.asyncio
async def test_publish_fans_out_to_two_subscribers(manager: JobManager) -> None:
    gate = threading.Event()

    def runner(ctx: JobContext) -> dict[str, Any]:
        gate.wait(2.0)  # let subscribers attach before any live event
        ctx.progress(1, 2, "half")
        ctx.publish("points", {"points": [{"date": "2020-01-01", "value": 1.0, "count": 5}]})
        ctx.progress(2, 2, "full")
        return {"cache_key": "k"}

    job_id = await manager.submit("timeseries", {}, runner)
    task1 = asyncio.create_task(_collect(manager.subscribe(job_id)))
    task2 = asyncio.create_task(_collect(manager.subscribe(job_id)))
    await asyncio.sleep(0.1)  # both attach and read their snapshot
    gate.set()

    events1, events2 = await asyncio.wait_for(asyncio.gather(task1, task2), timeout=5)

    for events in (events1, events2):
        names = [name for name, _ in events]
        assert names[0] == "progress"  # the one-shot snapshot
        assert "points" in names
        assert names[-1] == "done"
        assert events[-1][1] == {"status": "succeeded", "result": {"cache_key": "k"}}
        assert ("progress", {"done": 2, "total": 2, "message": "full"}) in events


@pytest.mark.asyncio
async def test_late_subscribe_gets_only_terminal(manager: JobManager) -> None:
    def runner(ctx: JobContext) -> dict[str, Any]:
        return {"v": 1}

    job_id = await manager.submit("timeseries", {}, runner)
    await _wait_terminal(manager, job_id)

    events = await asyncio.wait_for(_collect(manager.subscribe(job_id)), timeout=5)
    assert events == [("done", {"status": "succeeded", "result": {"v": 1}})]


@pytest.mark.asyncio
async def test_late_subscribe_on_failed_job_gets_error(manager: JobManager) -> None:
    def runner(ctx: JobContext) -> dict[str, Any]:
        raise JobError("nope")

    job_id = await manager.submit("timeseries", {}, runner)
    await _wait_terminal(manager, job_id)

    events = await asyncio.wait_for(_collect(manager.subscribe(job_id)), timeout=5)
    assert events == [("error", {"status": "failed", "detail": "nope"})]


# ── interrupted sweep (through the app) ───────────────────────


def test_interrupted_sweep_on_boot(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    engine = create_db_engine(data_dir / "openearth.db")
    migrate(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id="was-running",
                kind="timeseries",
                status="running",
                params_json="{}",
                created_at=utcnow_iso(),
                started_at=utcnow_iso(),
            )
        )
        session.add(
            Job(
                id="was-queued",
                kind="timeseries",
                status="queued",
                params_json="{}",
                created_at=utcnow_iso(),
            )
        )
        session.commit()
    engine.dispose()

    settings = Settings(_env_file=None, ee_project=None, data_dir=data_dir)
    app = create_app(settings=settings)
    with TestClient(app) as client:  # context manager runs the lifespan → sweep
        for job_id in ("was-running", "was-queued"):
            response = client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "interrupted"
            assert body["finished_at"] is not None


def test_get_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/api/jobs/does-not-exist").status_code == 404


# ── SSE over HTTP ─────────────────────────────────────────────


def _parse_sse_line(line: str, current_event: list[str | None], out: list[tuple[str, Any]]) -> None:
    """Accumulate one SSE line into *out*; ``current_event`` is a 1-slot cell."""
    if line.startswith(":"):  # comment / ping — ignore
        return
    if line.startswith("event:"):
        current_event[0] = line[len("event:") :].strip()
    elif line.startswith("data:"):
        data = json.loads(line[len("data:") :].strip())
        out.append((current_event[0] or "message", data))
        current_event[0] = None


@pytest.mark.asyncio
async def test_sse_stream_progress_then_done(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, ee_project=None, data_dir=tmp_path / "data")
    app = create_app(settings=settings)

    async def run() -> list[tuple[str, Any]]:
        async with app.router.lifespan_context(app):
            gate = threading.Event()

            def runner(ctx: JobContext) -> dict[str, Any]:
                gate.wait(2.0)  # hold until the stream has attached
                ctx.progress(1, 2, "chunk 1")
                ctx.publish(
                    "points",
                    {"points": [{"date": "2020-01-01", "value": 0.5, "count": 7}]},
                )
                return {"cache_key": "k"}

            job_id = await app.state.jobs.submit("timeseries", {}, runner)

            transport = httpx.ASGITransport(app=app)
            events: list[tuple[str, Any]] = []
            current: list[str | None] = [None]
            released = False
            async with (
                httpx.AsyncClient(transport=transport, base_url="http://test") as http,
                http.stream("GET", f"/api/jobs/{job_id}/events") as response,
            ):
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    before = len(events)
                    _parse_sse_line(line.rstrip("\r"), current, events)
                    if len(events) == before:
                        continue
                    # Release the runner once the snapshot has been delivered.
                    if not released and events[-1][0] == "progress":
                        gate.set()
                        released = True
                    if events[-1][0] in ("done", "error"):
                        break
                    if len(events) > 20:  # guard against a runaway stream
                        break
            return events

    events = await asyncio.wait_for(run(), timeout=10)
    names = [name for name, _ in events]
    assert names[0] == "progress"  # snapshot first
    assert ("progress", {"done": 1, "total": 2, "message": "chunk 1"}) in events
    assert "points" in names
    assert events[-1] == ("done", {"status": "succeeded", "result": {"cache_key": "k"}})
