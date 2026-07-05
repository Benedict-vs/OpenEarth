"""Job status, cancellation, listing, and the SSE event stream.

No Earth Engine here: a job's *runner* may touch EE (later stages), but
inspecting and cancelling jobs never does. Submission routes (stages 3, 6)
live with their domain and add ``ensure_ee`` themselves.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from openearth_api.deps import get_jobs
from openearth_api.jobs import JobManager  # runtime import: FastAPI evaluates the route annotations
from openearth_api.schemas import JobOut

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openearth_api.models import Job

router = APIRouter(tags=["jobs"])

# Keep the stream alive through proxies/load-balancers; also the heartbeat
# that lets the client notice a dropped connection.
_SSE_PING_SECONDS = 15


def _to_out(row: Job) -> JobOut:
    return JobOut(
        id=row.id,
        kind=row.kind,
        status=row.status,  # type: ignore[arg-type]  # DB text ⊆ JobStatus by construction
        progress_done=row.progress_done,
        progress_total=row.progress_total,
        message=row.message,
        result=json.loads(row.result_json) if row.result_json else None,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/jobs")
def list_jobs(
    jobs: Annotated[JobManager, Depends(get_jobs)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[JobOut]:
    return [_to_out(row) for row in jobs.list_jobs(limit)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, jobs: Annotated[JobManager, Depends(get_jobs)]) -> JobOut:
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")
    return _to_out(row)


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, jobs: Annotated[JobManager, Depends(get_jobs)]) -> JobOut:
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")
    await jobs.cancel(job_id)
    # Cancellation is cooperative and asynchronous; report the row as it
    # stands now (the client watches /events or re-polls for the transition).
    return _to_out(jobs.get(job_id) or row)


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str, jobs: Annotated[JobManager, Depends(get_jobs)]
) -> EventSourceResponse:
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")

    async def stream() -> AsyncIterator[dict[str, str]]:
        async for event, data in jobs.subscribe(job_id):
            yield {"event": event, "data": json.dumps(data)}

    return EventSourceResponse(stream(), ping=_SSE_PING_SECONDS)
