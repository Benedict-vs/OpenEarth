"""Timeseries endpoints: submit a series job, download its cached result.

``POST /timeseries`` needs Earth Engine (its runner reduces on EE), so it
validates + resolves at request time behind ``ensure_ee``. The result
download only reads the DB and cache, so it stays EE-free.
"""

from __future__ import annotations

from typing import Annotated, Literal

import diskcache
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from openearth_api.deps import ensure_ee, get_cache, get_jobs
from openearth_api.jobs import JobManager  # runtime import: FastAPI evaluates route annotations
from openearth_api.schemas import JobCreated, TimeseriesRequest, TimeseriesResultOut
from openearth_api.services.timeseries import submit_timeseries, timeseries_result

router = APIRouter(tags=["timeseries"])


@router.post("/timeseries", dependencies=[Depends(ensure_ee)])
async def submit_timeseries_route(
    body: TimeseriesRequest,
    jobs: Annotated[JobManager, Depends(get_jobs)],
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
) -> JobCreated:
    return await submit_timeseries(body, jobs, cache)


@router.get(
    "/timeseries/{job_id}/result",
    response_model=TimeseriesResultOut,
    responses={
        200: {"content": {"text/csv": {}, "application/vnd.apache.parquet": {}}},
        409: {"description": "Job not finished"},
        410: {"description": "Cached result evicted"},
    },
)
def timeseries_result_route(
    job_id: str,
    jobs: Annotated[JobManager, Depends(get_jobs)],
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
    format: Annotated[Literal["json", "csv", "parquet"], Query()] = "json",
) -> Response:
    return timeseries_result(job_id, format, jobs, cache)
