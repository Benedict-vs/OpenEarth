"""Methane Lab endpoints: sites, scene search, analyze job, detection feed.

Scene search and analyze submission touch Earth Engine, so they sit behind
``ensure_ee``; sites CRUD, the detection feed, overlay PNGs and the array
download only read the DB/disk/cache and stay EE-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

import diskcache
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from openearth.geometry import BBox
from openearth.settings import Settings
from openearth_api.deps import ensure_ee, get_app_settings, get_cache, get_db_engine, get_jobs
from openearth_api.jobs import JobManager  # runtime import: FastAPI evaluates route annotations
from openearth_api.schemas import (
    AnalyzeRequest,
    BBoxIn,
    DetectionDetailOut,
    DetectionOut,
    DetectionPatch,
    EmitPlumesOut,
    JobCreated,
    MlScanRequest,
    MlStatusOut,
    NoiseFloorOut,
    ReferenceEventOut,
    SceneInfoOut,
    ScreeningRequest,
    SiteIn,
    SiteOut,
    SitePatch,
    ValidationImportOut,
    ValidationOut,
)
from openearth_api.services import emit as svc_emit
from openearth_api.services import methane as svc
from openearth_api.services import ml as svc_ml

if TYPE_CHECKING:
    from sqlalchemy import Engine

router = APIRouter(tags=["methane"])

EngineDep = Annotated["Engine", Depends(get_db_engine)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CacheDep = Annotated[diskcache.Cache, Depends(get_cache)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]


# ── Sites ──


@router.get("/methane/sites")
def list_sites(engine: EngineDep) -> list[SiteOut]:
    return svc.list_sites(engine)


@router.post("/methane/sites", status_code=status.HTTP_201_CREATED)
def create_site(body: SiteIn, engine: EngineDep) -> SiteOut:
    return svc.create_site(body, engine)


@router.patch("/methane/sites/{site_id}")
def patch_site(site_id: int, body: SitePatch, engine: EngineDep) -> SiteOut:
    return svc.patch_site(site_id, body, engine)


@router.delete("/methane/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(site_id: int, engine: EngineDep) -> Response:
    svc.delete_site(site_id, engine)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/methane/sites/{site_id}/noise-floor")
def get_site_noise_floor(site_id: int, engine: EngineDep) -> NoiseFloorOut:
    return svc.get_site_floor(engine, site_id)


@router.get("/methane/sites/{site_id}/scenes", dependencies=[Depends(ensure_ee)])
def list_site_scenes(
    site_id: int,
    engine: EngineDep,
    start: Annotated[str, Query()],
    end: Annotated[str, Query()],
    max_cloud: Annotated[float, Query(ge=0, le=100)] = 80.0,
    # Optional analysis-area override (all four or none): scenes are listed over
    # the area actually analyzed, not the browse-scale site ROI, so a picked
    # scene is guaranteed to cover the chip.
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
) -> list[SceneInfoOut]:
    roi = None
    if west is not None and south is not None and east is not None and north is not None:
        roi = BBoxIn(west=west, south=south, east=east, north=north)
    elif any(c is not None for c in (west, south, east, north)):
        raise HTTPException(422, "Provide all of west/south/east/north or none.")
    return svc.list_scenes_for(engine, site_id, roi, start, end, max_cloud)


# ── Analyze ──


@router.post("/methane/analyze", dependencies=[Depends(ensure_ee)])
async def submit_analyze(
    body: AnalyzeRequest, jobs: JobsDep, engine: EngineDep, settings: SettingsDep
) -> JobCreated:
    return await svc.submit_analyze(body, jobs, engine, settings)


# ── Detection feed + detail ──


@router.get("/methane/detections")
def list_detections(
    engine: EngineDep,
    site_id: Annotated[int | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DetectionOut]:
    return svc.list_detections(engine, site_id, status, source, limit, offset)


@router.get("/methane/detections/{det_id}")
def get_detection(det_id: str, engine: EngineDep) -> DetectionDetailOut:
    return svc.get_detection_detail(engine, det_id)


@router.patch("/methane/detections/{det_id}")
def patch_detection(det_id: str, body: DetectionPatch, engine: EngineDep) -> DetectionDetailOut:
    return svc.patch_detection(engine, det_id, body)


@router.delete("/methane/detections/{det_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_detection(det_id: str, engine: EngineDep, settings: SettingsDep) -> Response:
    svc.delete_detection(engine, settings, det_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/methane/detections/{det_id}/overlay.png",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def detection_overlay(
    det_id: str,
    engine: EngineDep,
    settings: SettingsDep,
    cache: CacheDep,
    vmin: Annotated[float | None, Query()] = None,
    vmax: Annotated[float | None, Query()] = None,
) -> Response:
    png = svc.overlay_png(engine, settings, cache, det_id, vmin, vmax)
    return Response(content=png, media_type="image/png")


@router.get(
    "/methane/detections/{det_id}/array.npz",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def detection_array(det_id: str, engine: EngineDep, settings: SettingsDep) -> FileResponse:
    path = svc.detection_array_path(engine, settings, det_id)
    return FileResponse(
        path, media_type="application/octet-stream", filename=f"detection_{det_id}.npz"
    )


# ── EMIT plumes (Phase 6) ──


@router.get("/methane/emit/plumes", dependencies=[Depends(ensure_ee)])
def list_emit_plumes(
    cache: CacheDep,
    west: Annotated[float, Query(ge=-180, le=180)],
    south: Annotated[float, Query(ge=-90, le=90)],
    east: Annotated[float, Query(ge=-180, le=180)],
    north: Annotated[float, Query(ge=-90, le=90)],
    start: Annotated[str, Query()],
    end: Annotated[str, Query()],
) -> EmitPlumesOut:
    bbox = BBox(west, south, east, north)  # InvalidROIError → 422
    return svc_emit.list_plumes_out(bbox, start, end, cache)


@router.post("/methane/detections/{det_id}/emit-match", dependencies=[Depends(ensure_ee)])
def emit_match_detection(det_id: str, engine: EngineDep, cache: CacheDep) -> DetectionDetailOut:
    return svc_emit.match_detection(engine, cache, det_id)


# ── Screening ──


@router.post("/methane/screening", dependencies=[Depends(ensure_ee)])
async def submit_screening(body: ScreeningRequest, jobs: JobsDep) -> JobCreated:
    return await svc.submit_screening(body, jobs)


# ── ML tier (candidate ranker; never an autonomous detector) ──


@router.post("/methane/ml/scan", dependencies=[Depends(ensure_ee)])
async def submit_ml_scan(
    body: MlScanRequest, jobs: JobsDep, engine: EngineDep, settings: SettingsDep
) -> JobCreated:
    return await svc_ml.submit_ml_scan(body, jobs, engine, settings)


@router.get("/methane/ml/status")
def ml_status(settings: SettingsDep) -> MlStatusOut:
    return svc_ml.ml_status(settings)


# ── Validation ──


@router.post("/methane/validation/import")
async def import_validation(
    engine: EngineDep,
    file: Annotated[UploadFile, File()],
    source: Annotated[str, Form()],
    fmt: Annotated[Literal["csv", "geojson"], Form()],
    # Applies to unit-agnostic rate columns only; unit-declared columns
    # (`*_t_h`, `ch4_fluxrate`/`*_kg_h`) always self-describe. Default "auto"
    # drops agnostic rates rather than guess their unit.
    unit: Annotated[Literal["auto", "t_h", "kg_h"], Form()] = "auto",
) -> ValidationImportOut:
    data = await file.read()
    return svc.import_events(engine, data, source, fmt, unit)


@router.get("/methane/validation/events")
def list_validation_events(engine: EngineDep) -> list[ReferenceEventOut]:
    return svc.list_events(engine)


@router.post("/methane/detections/{det_id}/validate")
def validate_detection(det_id: str, engine: EngineDep) -> ValidationOut:
    return svc.validate_detection(engine, det_id)
