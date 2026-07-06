"""Export services: a GeoTIFF job, its download, and a synchronous PNG.

GeoTIFF is a background job (a large ROI streams window-by-window through
``computePixels`` — real work), so it reuses the stage-1 job manager: the route
validates and builds the image up front behind ``ensure_ee``, then a runner
writes ``data_dir/exports/{file_id}.tif`` and reports one progress tick per
window. The finished file is served by :func:`export_download`.

PNG is a single server-rendered thumbnail fetch — no job, just the existing
thumbnail pipeline with an ``attachment`` disposition.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from openearth.errors import JobError
from openearth.export import export_geotiff
from openearth_api.schemas import ExportGeotiffRequest, JobCreated, ThumbnailRequest, TilesRequest
from openearth_api.services.thumbnails import render_thumbnail
from openearth_api.services.tiles import build_image, resolve_catalog

if TYPE_CHECKING:
    from pathlib import Path

    import diskcache

    from openearth.settings import Settings
    from openearth_api.jobs import JobContext, JobManager


def _as_tiles_request(req: ExportGeotiffRequest) -> TilesRequest:
    """View the export request through the shared composite builder's model."""
    return TilesRequest(
        dataset=req.dataset,
        product=req.product,
        roi=req.roi,
        composite=req.composite,
        dates=req.dates,
        target_date=req.target_date,
        half_window_days=req.half_window_days,
        timestamp_ms=req.timestamp_ms,
    )


def _date_label(req: ExportGeotiffRequest | ThumbnailRequest) -> str:
    """A filename-safe span label reflecting the composite mode."""
    if req.composite == "mean" and req.dates is not None:
        return f"{req.dates.start}_{req.dates.end}"
    if req.composite == "date_window" and req.target_date is not None:
        return f"{req.target_date}_pm{req.half_window_days}d"
    if req.composite == "single_scene" and req.timestamp_ms is not None:
        return f"ts{req.timestamp_ms}"
    return "composite"


def _download_stem(req: ExportGeotiffRequest | ThumbnailRequest) -> str:
    return f"{req.dataset}_{req.product}_{_date_label(req)}"


def _exports_dir(settings: Settings) -> Path:
    return settings.data_dir / "exports"


async def submit_export_geotiff(
    req: ExportGeotiffRequest, jobs: JobManager, settings: Settings
) -> JobCreated:
    """Validate + build the image, then submit the windowed-write job."""
    dataset, spec = resolve_catalog(req.dataset, req.product)  # 404 / 422 (builder)
    roi = req.roi.to_domain()  # 422 on malformed geometry
    image = build_image(_as_tiles_request(req), roi, spec)  # per-mode 422 (dates/target/timestamp)
    scale_m = req.scale_m if req.scale_m is not None else dataset.default_scale_m

    file_name = f"{uuid4().hex}.tif"
    dest = _exports_dir(settings) / file_name
    download_name = f"{_download_stem(req)}.tif"

    def runner(ctx: JobContext) -> dict[str, Any]:
        _exports_dir(settings).mkdir(parents=True, exist_ok=True)

        def on_progress(done: int, total: int) -> None:
            if ctx.cancelled.is_set():
                raise JobError("cancelled")
            ctx.progress(done, total, f"window {done}/{total}")

        export_geotiff(image, spec, roi, scale_m, dest, on_progress=on_progress)
        return {"filename": file_name, "download_name": download_name}

    params = {
        "dataset": req.dataset,
        "product": req.product,
        "scale_m": scale_m,
        "filename": file_name,
        "download_name": download_name,
    }
    job_id = await jobs.submit("export_geotiff", params, runner)
    return JobCreated(job_id=job_id)


def export_download(job_id: str, jobs: JobManager, settings: Settings) -> FileResponse:
    """Serve a finished export's GeoTIFF; 404/409/410 mirror the series result."""
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")
    if row.status != "succeeded":
        raise HTTPException(
            status_code=409,
            detail=f"Export job is not finished (status={row.status!r}); watch its events.",
        )

    params = json.loads(row.params_json)
    result = json.loads(row.result_json) if row.result_json else {}
    file_name = result.get("filename") or params.get("filename")
    path = _exports_dir(settings) / file_name if file_name else None
    if path is None or not path.exists():
        raise HTTPException(
            status_code=410,
            detail="The exported file was removed; re-run the export to regenerate it.",
        )

    download_name = params.get("download_name") or file_name
    return FileResponse(path, media_type="image/tiff", filename=download_name)


def export_png(req: ThumbnailRequest, cache: diskcache.Cache) -> Response:
    """Render the composite to a PNG and offer it as a download."""
    png = render_thumbnail(req, cache)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{_download_stem(req)}.png"'},
    )
