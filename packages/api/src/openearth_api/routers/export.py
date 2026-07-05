"""Export endpoints: GeoTIFF (async job + download) and PNG (synchronous).

Submission and PNG rendering touch Earth Engine, so they sit behind
``ensure_ee``. The download only reads the DB and disk, so it stays EE-free.
"""

from __future__ import annotations

from typing import Annotated

import diskcache
from fastapi import APIRouter, Depends, Response

from openearth.settings import Settings
from openearth_api.deps import ensure_ee, get_app_settings, get_cache, get_jobs
from openearth_api.jobs import JobManager  # runtime import: FastAPI evaluates route annotations
from openearth_api.schemas import ExportGeotiffRequest, JobCreated, ThumbnailRequest
from openearth_api.services.export import export_download, export_png, submit_export_geotiff

router = APIRouter(tags=["export"])


@router.post("/export/geotiff", dependencies=[Depends(ensure_ee)])
async def export_geotiff_route(
    body: ExportGeotiffRequest,
    jobs: Annotated[JobManager, Depends(get_jobs)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> JobCreated:
    return await submit_export_geotiff(body, jobs, settings)


@router.get(
    "/export/{job_id}/download",
    response_class=Response,
    responses={
        200: {"content": {"image/tiff": {}}},
        409: {"description": "Job not finished"},
        410: {"description": "Exported file removed"},
    },
)
def export_download_route(
    job_id: str,
    jobs: Annotated[JobManager, Depends(get_jobs)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    return export_download(job_id, jobs, settings)


@router.post(
    "/export/png",
    dependencies=[Depends(ensure_ee)],
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def export_png_route(
    body: ThumbnailRequest,
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
) -> Response:
    return export_png(body, cache)
