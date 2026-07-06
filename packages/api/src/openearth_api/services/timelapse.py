"""Timelapse services: validate + submit the render job, gallery, artifacts.

The core render/encode entry points (``render_frames``, ``encode_movie``) and
``frame_windows`` are imported by name so offline tests fake them at this module
level. The render runner inserts and updates its own ``renders`` row from the
worker thread via short-lived ``Session``s (WAL + busy_timeout, detections
precedent) — the manager-owned ``jobs`` table stays event-loop-only, but a
render's natural owner is its runner.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from openearth.timelapse import (
    MAX_DIM_GIF,
    AnnotationOptions,
    encode_movie,
    frame_windows,
    render_frames,
)
from openearth_api.models import Render, utcnow_iso
from openearth_api.schemas import (
    RenderDetailOut,
    RenderOut,
    TimelapseCreated,
    TimelapseRequest,
)
from openearth_api.services.tiles import resolve_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Engine

    from openearth.settings import Settings
    from openearth_api.jobs import JobContext, JobManager

# A GIF holds every frame in RAM at encode time; cap the frame count so a long
# GIF can't blow memory (video formats stream and have no such cap).
_MAX_GIF_FRAMES = 200

_MOVIE_MEDIA_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "gif": "image/gif"}


def _renders_dir(settings: Settings) -> Path:
    return settings.data_dir / "timelapse"


def _render_dir(settings: Settings, render_id: str) -> Path:
    return _renders_dir(settings) / render_id


def _default_title(req: TimelapseRequest) -> str:
    return f"{req.dataset} · {req.product} · {req.dates.start}→{req.dates.end}"


async def submit_timelapse(
    req: TimelapseRequest, jobs: JobManager, engine: Engine, settings: Settings
) -> TimelapseCreated:
    """Validate the request, then submit the ``timelapse`` render job.

    Validation order mirrors ``submit_export_geotiff``: catalog resolve
    (404 unknown / 422 builder products), ROI geometry (422), then the frame
    windows are computed up front (422 on a bad range, < 2 frames, or a count
    over the budget) — all before any Earth Engine work.
    """
    resolve_catalog(req.dataset, req.product)  # 404 unknown / 422 builder product
    roi = req.roi.to_domain()  # 422 on malformed geometry

    max_dim = min(req.max_dim, MAX_DIM_GIF) if req.format == "gif" else req.max_dim
    try:
        windows = frame_windows(
            req.dates.start,
            req.dates.end,
            mode=req.step.mode,
            interval_days=req.step.interval_days,
            window_days=req.step.window_days,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(windows) < 2:
        raise HTTPException(422, "A timelapse needs at least 2 frames; widen the date range.")
    if req.format == "gif" and len(windows) > _MAX_GIF_FRAMES:
        raise HTTPException(
            422,
            f"GIF is capped at {_MAX_GIF_FRAMES} frames ({len(windows)} requested); "
            "use mp4/webm or a coarser step.",
        )

    render_id = uuid4().hex
    title = req.title or _default_title(req)
    annotations = AnnotationOptions(
        date_label=req.annotations.date_label,
        colorbar=req.annotations.colorbar,
        scale_bar=req.annotations.scale_bar,
        attribution=req.annotations.attribution,
    )
    even_dims = req.format in ("mp4", "webm")
    params_json = json.dumps(req.model_dump(mode="json"))
    roi_json = req.roi.model_dump_json()

    def runner(ctx: JobContext) -> dict[str, Any]:
        out_dir = _render_dir(settings, render_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        _insert_running_row(engine, render_id, title, req, params_json, roi_json)

        try:
            manifest = render_frames(
                req.dataset,
                req.product,
                roi,
                windows,
                out_dir=out_dir,
                max_dim=max_dim,
                even_dims=even_dims,
                vis_min=req.vis_min,
                vis_max=req.vis_max,
                annotations=annotations,
                on_progress=lambda done, total: ctx.progress(done, total, f"frame {done}/{total}"),
                on_frame=lambda index, status, total: ctx.publish(
                    "frame", {"index": index, "status": status, "total": total}
                ),
                should_cancel=ctx.cancelled.is_set,
            )
            movie_path = out_dir / f"movie.{req.format}"
            encode_movie(manifest.frame_paths, movie_path, fmt=req.format, fps=req.fps)
        except BaseException:
            status = "cancelled" if ctx.cancelled.is_set() else "failed"
            _update_row(engine, render_id, status=status)
            raise

        _update_row(
            engine,
            render_id,
            status="succeeded",
            frame_count=manifest.rendered_count,
            movie_bytes=movie_path.stat().st_size,
        )
        return {"render_id": render_id}

    job_id = await jobs.submit("timelapse", json.loads(params_json), runner)
    return TimelapseCreated(job_id=job_id, render_id=render_id)


def _insert_running_row(
    engine: Engine,
    render_id: str,
    title: str,
    req: TimelapseRequest,
    params_json: str,
    roi_json: str,
) -> None:
    now = utcnow_iso()
    row = Render(
        id=render_id,
        title=title,
        dataset=req.dataset,
        product=req.product,
        params_json=params_json,
        roi_json=roi_json,
        status="running",
        frame_count=None,
        fps=req.fps,
        format=req.format,
        movie_bytes=None,
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()


def _update_row(engine: Engine, render_id: str, **fields: Any) -> None:
    with Session(engine) as session:
        row = session.get(Render, render_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = utcnow_iso()
        session.add(row)
        session.commit()


# ── Gallery + artifacts ──


def _render_out(row: Render) -> RenderOut:
    return RenderOut(
        id=row.id,
        title=row.title,
        dataset=row.dataset,
        product=row.product,
        status=row.status,  # type: ignore[arg-type]
        frame_count=row.frame_count,
        fps=row.fps,
        format=row.format,  # type: ignore[arg-type]
        movie_bytes=row.movie_bytes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_renders(engine: Engine) -> list[RenderOut]:
    with Session(engine) as session:
        rows = session.exec(
            select(Render).order_by(Render.created_at.desc())  # type: ignore[attr-defined]
        ).all()
        return [_render_out(r) for r in rows]


def _require_render(session: Session, render_id: str) -> Render:
    row = session.get(Render, render_id)
    if row is None:
        raise HTTPException(404, f"No render {render_id!r}.")
    return row


def get_render_detail(engine: Engine, settings: Settings, render_id: str) -> RenderDetailOut:
    from openearth_api.schemas import ROI_ADAPTER

    with Session(engine) as session:
        row = _require_render(session, render_id)
        base = _render_out(row).model_dump()
        manifest_path = _render_dir(settings, render_id) / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        return RenderDetailOut(
            **base,
            roi=ROI_ADAPTER.validate_json(row.roi_json),
            params=json.loads(row.params_json),
            manifest=manifest,
        )


def frame_response(settings: Settings, render_id: str, index: int) -> FileResponse:
    path = _render_dir(settings, render_id) / f"frame_{index:04d}.png"
    if not path.exists():
        raise HTTPException(404, f"No frame {index} in render {render_id!r}.")
    # Frames are immutable once written — let clients cache them hard.
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000"}
    )


def download_response(engine: Engine, settings: Settings, render_id: str) -> FileResponse:
    with Session(engine) as session:
        row = _require_render(session, render_id)
    if row.status != "succeeded":
        raise HTTPException(
            409, f"Render is not finished (status={row.status!r}); watch its events."
        )

    movie = _render_dir(settings, render_id) / f"movie.{row.format}"
    if not movie.exists():
        raise HTTPException(410, "The rendered movie was removed; re-run the timelapse.")

    params = json.loads(row.params_json)
    dates = params.get("dates", {})
    download_name = (
        f"{row.dataset}_{row.product}_{dates.get('start')}_{dates.get('end')}.{row.format}"
    )
    return FileResponse(movie, media_type=_MOVIE_MEDIA_TYPES[row.format], filename=download_name)


def delete_render(engine: Engine, settings: Settings, render_id: str) -> None:
    with Session(engine) as session:
        row = _require_render(session, render_id)
        if row.status == "running":
            raise HTTPException(409, "Cannot delete a render while it is still running.")
        session.delete(row)
        session.commit()
    shutil.rmtree(_render_dir(settings, render_id), ignore_errors=True)
