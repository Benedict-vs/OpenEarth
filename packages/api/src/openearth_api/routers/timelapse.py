"""Timelapse Studio endpoints: submit a render job, gallery, frames, download.

Submitting a render touches Earth Engine (it renders composites), so it sits
behind ``ensure_ee``; the gallery, frame PNGs, movie download, and delete only
read the DB/disk and stay EE-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse

from openearth.settings import Settings
from openearth_api.deps import ensure_ee, get_app_settings, get_db_engine, get_jobs
from openearth_api.jobs import JobManager  # runtime import: FastAPI evaluates route annotations
from openearth_api.schemas import (
    RenderDetailOut,
    RenderOut,
    RenderUpdateIn,
    TimelapseCreated,
    TimelapseRequest,
)
from openearth_api.services import timelapse as svc

if TYPE_CHECKING:
    from sqlalchemy import Engine

router = APIRouter(tags=["timelapse"])

EngineDep = Annotated["Engine", Depends(get_db_engine)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]


@router.post("/timelapse", dependencies=[Depends(ensure_ee)])
async def submit_timelapse(
    body: TimelapseRequest, jobs: JobsDep, engine: EngineDep, settings: SettingsDep
) -> TimelapseCreated:
    return await svc.submit_timelapse(body, jobs, engine, settings)


@router.get("/timelapse")
def list_renders(engine: EngineDep) -> list[RenderOut]:
    return svc.list_renders(engine)


@router.get("/timelapse/{render_id}")
def get_render(render_id: str, engine: EngineDep, settings: SettingsDep) -> RenderDetailOut:
    return svc.get_render_detail(engine, settings, render_id)


@router.patch("/timelapse/{render_id}")
def update_render(render_id: str, body: RenderUpdateIn, engine: EngineDep) -> RenderOut:
    return svc.update_render(engine, render_id, body.title)


@router.get(
    "/timelapse/{render_id}/frames/{index}",
    responses={200: {"content": {"image/png": {}}}},
)
def get_frame(render_id: str, index: int, settings: SettingsDep) -> FileResponse:
    return svc.frame_response(settings, render_id, index)


@router.get(
    "/timelapse/{render_id}/download",
    responses={200: {"content": {"video/mp4": {}, "video/webm": {}, "image/gif": {}}}},
)
def download_movie(render_id: str, engine: EngineDep, settings: SettingsDep) -> FileResponse:
    return svc.download_response(engine, settings, render_id)


@router.delete("/timelapse/{render_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_render(render_id: str, engine: EngineDep, settings: SettingsDep) -> None:
    svc.delete_render(engine, settings, render_id)
