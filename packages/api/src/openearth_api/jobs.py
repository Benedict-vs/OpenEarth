"""In-process background job manager with a thread-safe progress channel.

Concurrency model (do not casually change — the invariants are load-bearing):

- **One writer.** Only the event-loop thread touches the SQLite database. A
  job's runner executes in a worker thread (via ``asyncio.to_thread``) and
  never sees a ``Session``; it reports progress through :class:`JobContext`,
  whose calls hop back onto the loop with ``call_soon_threadsafe``. WAL plus
  a single-threaded writer means no cross-thread session juggling.

- **One consumer per job.** Every job has an internal ``asyncio.Queue`` fed
  by the worker thread (progress/points) and by the loop side (the terminal
  event). A per-job consumer coroutine drains it in order, persists progress
  (throttled) and terminal state, and fans each event out to live SSE
  subscribers. Because the consumer and ``subscribe`` both run on the single
  loop thread, "is this job already terminal?" and "register a subscriber"
  compose without a lock.

- **Cooperative cancellation.** ``cancel`` sets a ``threading.Event`` the
  runner is expected to check at chunk boundaries. If the runner raises after
  the flag is set, the job is ``cancelled`` regardless of the exception type;
  a runner that ignores the flag and succeeds anyway is honoured as success.

- **Progressive results are previews.** ``points`` events are fanned out live
  but never persisted or replayed; a late/reconnecting subscriber that missed
  them must refetch the full result. This keeps the manager generic and its
  memory footprint flat.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import ee
from sqlmodel import Session, select

from openearth.errors import JobError, classify_ee_error
from openearth_api.models import TERMINAL_STATUSES, Job, utcnow_iso

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import Engine

# Cap concurrently *running* jobs so a stack of exports can't starve
# interactive tile mints. EE round-trips are already bounded globally by the
# core semaphore; this is the only additional brake (plan.md — resist adding
# per-feature pools).
MAX_RUNNING_JOBS = 4

# Minimum wall-clock gap between progress DB writes. Terminal states always
# persist regardless of this throttle.
_PROGRESS_PERSIST_INTERVAL_S = 0.25

# Grace period for in-flight jobs to unwind on shutdown before we give up and
# mark them ``interrupted``.
_SHUTDOWN_GRACE_S = 5.0

Runner = Callable[["JobContext"], "dict[str, Any] | None"]

# Internal queue signal (not part of the SSE wire vocabulary). A subscriber
# queue carries ``(event, data)`` items; ``None`` is its close sentinel.
_TERMINAL = "__terminal__"


class JobContext:
    """Handed to a runner; every method is safe to call from a worker thread.

    ``progress`` / ``publish`` marshal their payload back onto the event loop
    so the single-writer/single-consumer invariants hold even though the
    runner runs off-loop.
    """

    def __init__(self, post: Callable[[str, dict[str, Any]], None]) -> None:
        self.cancelled = threading.Event()
        self._post = post

    def progress(self, done: int, total: int, message: str | None = None) -> None:
        self._post("progress", {"done": done, "total": total, "message": message})

    def publish(self, event: str, data: dict[str, Any]) -> None:
        self._post(event, data)


@dataclass
class _Job:
    """Live, in-memory state for one job. Complements the persisted row."""

    id: str
    ctx: JobContext
    runner: Runner
    queue: asyncio.Queue[tuple[str, dict[str, Any]]]
    subscribers: set[asyncio.Queue[tuple[str, dict[str, Any]] | None]] = field(default_factory=set)
    snapshot: dict[str, Any] = field(
        default_factory=lambda: {"done": 0, "total": 0, "message": None}
    )
    terminal_event: asyncio.Event = field(default_factory=asyncio.Event)
    terminal_payload: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None
    last_persist: float = 0.0


class JobManager:
    """Owns the job table and the in-flight job tasks for one app instance."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._jobs: dict[str, _Job] = {}
        self._run_semaphore = asyncio.Semaphore(MAX_RUNNING_JOBS)

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Sweep any rows left ``queued``/``running`` by a prior process to
        ``interrupted`` so a restart doesn't leave phantom active jobs."""
        with Session(self._engine) as session:
            stale = session.exec(
                select(Job).where(Job.status.in_(("queued", "running")))  # type: ignore[attr-defined]
            ).all()
            for row in stale:
                row.status = "interrupted"
                row.finished_at = utcnow_iso()
                session.add(row)
            session.commit()

    async def stop(self) -> None:
        """Signal cancellation to every in-flight job, await a grace period,
        then persist ``interrupted`` for anything that didn't finish."""
        jobs = list(self._jobs.values())
        for job in jobs:
            job.ctx.cancelled.set()
        tasks = [job.task for job in jobs if job.task is not None and not job.task.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_SHUTDOWN_GRACE_S)
            for task in pending:
                task.cancel()
        with Session(self._engine) as session:
            for job in jobs:
                if job.terminal_event.is_set():
                    continue
                row = session.get(Job, job.id)
                if row is not None and row.status in ("queued", "running"):
                    row.status = "interrupted"
                    row.finished_at = utcnow_iso()
                    session.add(row)
            session.commit()

    # ── public API ───────────────────────────────────────────────

    async def submit(self, kind: str, params: dict[str, Any], runner: Runner) -> str:
        """Persist a ``queued`` row and schedule the job; return its id."""
        job_id = uuid4().hex
        with Session(self._engine) as session:
            session.add(
                Job(
                    id=job_id,
                    kind=kind,
                    status="queued",
                    params_json=json.dumps(params, default=str),
                    created_at=utcnow_iso(),
                )
            )
            session.commit()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def post(event: str, data: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        job = _Job(id=job_id, ctx=JobContext(post), runner=runner, queue=queue)
        self._jobs[job_id] = job
        job.task = loop.create_task(self._lifecycle(job))
        return job_id

    def get(self, job_id: str) -> Job | None:
        with Session(self._engine) as session:
            return session.get(Job, job_id)

    def list_jobs(self, limit: int) -> list[Job]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(Job).order_by(Job.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
            ).all()
            return list(rows)

    async def cancel(self, job_id: str) -> None:
        """Request cancellation. Idempotent; a no-op on unknown/finished jobs.

        Setting the flag is enough: a queued job is caught before it starts,
        a running job's runner observes it at the next chunk boundary.
        """
        job = self._jobs.get(job_id)
        if job is not None:
            job.ctx.cancelled.set()

    async def subscribe(self, job_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield SSE ``(event, data)`` pairs for a job until it terminates.

        Terminal already → the single terminal event, then close. Otherwise a
        one-shot ``progress`` snapshot, then live events, then close. The
        terminal check and subscriber registration below run without an
        intervening ``await``, so the consumer cannot slip a terminal event
        past a just-registered subscriber.
        """
        job = self._jobs.get(job_id)
        if job is None or job.terminal_event.is_set():
            row = self.get(job_id)
            if row is None:
                return
            yield _terminal_wire(
                row.status,
                json.loads(row.result_json) if row.result_json else {},
                row.error or row.status,
            )
            return

        sub: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        job.subscribers.add(sub)
        try:
            yield ("progress", dict(job.snapshot))
            while True:
                item = await sub.get()
                if item is None:  # close sentinel — job reached terminal
                    return
                yield item
        finally:
            job.subscribers.discard(sub)

    # ── internals ────────────────────────────────────────────────

    async def _lifecycle(self, job: _Job) -> None:
        """Drive one job start→terminal. Runs as a per-job ``asyncio.Task``."""
        consumer = asyncio.create_task(self._consume(job))
        try:
            async with self._run_semaphore:
                if job.ctx.cancelled.is_set():
                    payload = {"status": "cancelled", "detail": "cancelled"}
                else:
                    self._write(job.id, status="running", started_at=utcnow_iso())
                    payload = await self._execute(job)
                job.queue.put_nowait((_TERMINAL, payload))
            await consumer
        finally:
            self._jobs.pop(job.id, None)

    async def _execute(self, job: _Job) -> dict[str, Any]:
        """Run the runner off-loop and classify the outcome into a payload."""
        result: dict[str, Any] | None = None
        status = "succeeded"
        detail: str | None = None
        try:
            result = await asyncio.to_thread(job.runner, job.ctx)
        except JobError as exc:
            status, detail = "failed", str(exc)
        except ee.EEException as exc:
            _, detail = classify_ee_error(exc)
            status = "failed"
        except Exception as exc:  # any other runner failure → failed job
            status, detail = "failed", str(exc)

        # A raised runner while cancellation was requested is a cancellation,
        # whatever the exception type. A runner that ignored the flag and
        # succeeded is honoured as success.
        if status != "succeeded" and job.ctx.cancelled.is_set():
            return {"status": "cancelled", "detail": "cancelled"}
        if status == "succeeded":
            return {"status": "succeeded", "result": result or {}}
        return {"status": status, "detail": detail}

    async def _consume(self, job: _Job) -> None:
        """Drain the job's event queue: persist, then fan out to subscribers."""
        while True:
            event, data = await job.queue.get()
            if event == _TERMINAL:
                self._persist_terminal(job, data)
                job.terminal_payload = data
                job.terminal_event.set()
                wire = _terminal_wire(
                    data["status"], data.get("result", {}), data.get("detail", "")
                )
                self._fan_out(job, wire)
                for sub in list(job.subscribers):
                    sub.put_nowait(None)  # close each subscriber's stream
                job.subscribers.clear()
                return
            if event == "progress":
                job.snapshot = data
                self._maybe_persist_progress(job, data)
            self._fan_out(job, (event, data))

    def _fan_out(self, job: _Job, item: tuple[str, dict[str, Any]]) -> None:
        for sub in job.subscribers:
            sub.put_nowait(item)

    def _maybe_persist_progress(self, job: _Job, data: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - job.last_persist < _PROGRESS_PERSIST_INTERVAL_S:
            return
        job.last_persist = now
        self._write(
            job.id,
            progress_done=int(data.get("done", 0)),
            progress_total=int(data.get("total", 0)),
            message=data.get("message"),
        )

    def _persist_terminal(self, job: _Job, payload: dict[str, Any]) -> None:
        status = payload["status"]
        fields: dict[str, Any] = {"status": status, "finished_at": utcnow_iso()}
        if status == "succeeded":
            fields["result_json"] = json.dumps(payload.get("result") or {}, default=str)
        else:
            fields["error"] = payload.get("detail")
        self._write(job.id, **fields)

    def _write(self, job_id: str, **fields: Any) -> None:
        """The one and only DB writer — event-loop thread exclusively."""
        with Session(self._engine) as session:
            row = session.get(Job, job_id)
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            session.add(row)
            session.commit()


def _terminal_wire(status: str, result: dict[str, Any], detail: str) -> tuple[str, dict[str, Any]]:
    """Map a terminal status to its SSE event name and data payload."""
    if status == "succeeded":
        return ("done", {"status": status, "result": result})
    return ("error", {"status": status, "detail": detail})


__all__ = ["TERMINAL_STATUSES", "JobContext", "JobManager", "Runner"]
