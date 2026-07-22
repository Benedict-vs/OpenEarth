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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from openearth.catalog import get_product
from openearth.geometry import BBox
from openearth.timelapse import (
    DEFAULT_DEFLICKER_STRENGTH,
    DRAFT_MAX_DIM,
    MAX_DIM_4K,
    MAX_DIM_GIF,
    AnnotationOptions,
    MovieFormat,
    PostOptions,
    compose_extra_frames,
    encode_movie,
    frame_windows,
    native_max_dim,
    plan_fps,
    render_frames,
)
from openearth.timelapse_post import GradeOptions
from openearth_api.cache import cache_key, roi_key_part
from openearth_api.models import Render, utcnow_iso
from openearth_api.schemas import (
    ExtrasIn,
    PreflightOut,
    PreflightRequest,
    PreflightWindowOut,
    RenderDetailOut,
    RenderOut,
    TimelapseCreated,
    TimelapseRequest,
)
from openearth_api.services.tiles import resolve_catalog

if TYPE_CHECKING:
    from pathlib import Path

    import diskcache
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


def _parse_tint(cloud_display: str) -> tuple[int, int, int] | None:
    """``tint:#RRGGBB`` → an (r, g, b) hole-flag colour; else ``None``."""
    if cloud_display.startswith("tint:#"):
        h = cloud_display[6:]
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return None


def _build_post_options(req: TimelapseRequest, product_is_rgb: bool) -> PostOptions:
    """Compile the request knobs to core :class:`PostOptions` (422 on the honesty wall)."""
    grade = (
        GradeOptions(
            curve=req.grade.curve,
            brightness=req.grade.brightness,
            contrast=req.grade.contrast,
            saturation=req.grade.saturation,
        )
        if req.grade is not None
        else None
    )
    post = PostOptions(
        gap_fill=req.gap_fill,
        deflicker_strength=DEFAULT_DEFLICKER_STRENGTH if req.deflicker else 0.0,
        grade=grade,
        tint_hole_color=_parse_tint(req.cloud_display),
    )
    if post.modifies_pixels() and not product_is_rgb:
        raise HTTPException(
            422,
            f"Post-processing (gap-fill / deflicker / grade / hole tint) is display-only; "
            f"{req.dataset}/{req.product} is a scientific product — use an RGB product.",
        )
    return post


@dataclass(frozen=True)
class _RenderPlan:
    """The submit-time compiled render settings (draft, native lock, pacing)."""

    max_dim: int
    fps: int
    fmt: MovieFormat
    tween: int
    extras: ExtrasIn


def _compile_plan(req: TimelapseRequest, n_windows: int) -> _RenderPlan:
    draft = req.draft
    fmt = "mp4" if draft else req.format  # draft is always a quick mp4 (decision 10)
    # Decision 9 REVERSED (2026-07-22 acceptance review): the native clamp is gone —
    # EE's getThumbURL upscales the native data smoothly and the larger frame plus a
    # decent encode simply looks better. Honesty moved to the manifest's
    # native_max_dim ("render 1080 px · native 445 px"), not to a hard cap.
    requested_dim = min(req.max_dim, DRAFT_MAX_DIM) if draft else req.max_dim
    max_dim = min(requested_dim, MAX_DIM_4K)
    if fmt == "gif":
        max_dim = min(max_dim, MAX_DIM_GIF)
    return _RenderPlan(
        max_dim=max_dim,
        fps=plan_fps(n_windows, duration_s=req.duration_s, fps=req.fps),
        fmt=fmt,
        tween=0 if draft else req.tween,
        extras=ExtrasIn() if draft else req.extras,
    )


def _encode_all(manifest: Any, out_dir: Path, plan: _RenderPlan, extras: ExtrasIn) -> int:
    """Encode the hero movie (+ cards/watermark) and any crop variants; return hero bytes."""
    frames = manifest.frame_paths
    card_hold = max(1, round(plan.fps * 1.5))
    has_overlays = bool(extras.title_card or extras.end_card or extras.watermark)
    main = out_dir / f"movie.{plan.fmt}"

    if has_overlays:
        work = out_dir / ".extras_main"
        eff = compose_extra_frames(
            frames,
            work,
            watermark=extras.watermark,
            title_card=extras.title_card,
            end_card=extras.end_card,
            card_hold=card_hold,
        )
        encode_movie(eff, main, fmt=plan.fmt, fps=plan.fps, tween=plan.tween)
        shutil.rmtree(work, ignore_errors=True)
    else:
        encode_movie(frames, main, fmt=plan.fmt, fps=plan.fps, tween=plan.tween)

    for crop in extras.crops:
        safe = crop.replace(":", "_")
        work = out_dir / f".extras_{safe}"
        eff = compose_extra_frames(
            frames,
            work,
            crop=crop,
            watermark=extras.watermark,
            title_card=extras.title_card,
            end_card=extras.end_card,
            card_hold=card_hold,
        )
        encode_movie(
            eff, out_dir / f"movie_{safe}.{plan.fmt}", fmt=plan.fmt, fps=plan.fps, tween=plan.tween
        )
        shutil.rmtree(work, ignore_errors=True)

    return main.stat().st_size


async def submit_timelapse(
    req: TimelapseRequest, jobs: JobManager, engine: Engine, settings: Settings
) -> TimelapseCreated:
    """Validate + compile the request, then submit the ``timelapse`` render job.

    Validation order mirrors ``submit_export_geotiff``: catalog resolve
    (404 unknown / 422 builder products), ROI geometry (422), frame windows
    (422 on a bad range / < 2 frames / over budget), then the Phase-10 compile —
    honesty wall (422 post on non-RGB), draft/native/pacing plan, GIF cap — all
    before any Earth Engine work.
    """
    resolve_catalog(req.dataset, req.product)  # 404 unknown / 422 builder product
    roi = req.roi.to_domain()  # 422 on malformed geometry
    bbox = roi if isinstance(roi, BBox) else roi.bounds

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

    product_is_rgb = get_product(req.dataset, req.product).is_rgb
    post = _build_post_options(req, product_is_rgb)  # 422 on the honesty wall
    fallback = "hls" if req.fallback_source else None
    plan = _compile_plan(req, len(windows))
    native_dim = native_max_dim(bbox, req.dataset)

    # The GIF cap is on the *post-smoothing* frame count — tween inserts
    # (n-1)*tween cross-fades that all live in RAM at encode time.
    expanded = len(windows) + (len(windows) - 1) * plan.tween
    if plan.fmt == "gif" and expanded > _MAX_GIF_FRAMES:
        detail = (
            f"GIF is capped at {_MAX_GIF_FRAMES} frames ({expanded} after "
            f"{plan.tween}× smoothing); use mp4/webm, a coarser step, or less smoothing."
            if plan.tween
            else f"GIF is capped at {_MAX_GIF_FRAMES} frames ({expanded} requested); "
            "use mp4/webm or a coarser step."
        )
        raise HTTPException(422, detail)

    render_id = uuid4().hex
    title = req.title or _default_title(req)
    annotations = AnnotationOptions(
        date_label=req.annotations.date_label,
        colorbar=req.annotations.colorbar,
        scale_bar=req.annotations.scale_bar,
        attribution=req.annotations.attribution,
    )
    even_dims = plan.fmt in ("mp4", "webm")
    params_json = json.dumps(req.model_dump(mode="json"))
    roi_json = req.roi.model_dump_json()

    def runner(ctx: JobContext) -> dict[str, Any]:
        out_dir = _render_dir(settings, render_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        _insert_running_row(
            engine, render_id, title, req, params_json, roi_json, plan.fps, plan.fmt
        )

        try:
            manifest = render_frames(
                req.dataset,
                req.product,
                roi,
                windows,
                out_dir=out_dir,
                max_dim=plan.max_dim,
                even_dims=even_dims,
                vis_min=req.vis_min,
                vis_max=req.vis_max,
                annotations=annotations,
                composite_mode=req.composite,
                post=post,
                fallback_source=fallback,
                native_max_dim=native_dim,
                on_progress=lambda done, total: ctx.progress(done, total, f"frame {done}/{total}"),
                on_frame=lambda index, status, total: ctx.publish(
                    "frame", {"index": index, "status": status, "total": total}
                ),
                should_cancel=ctx.cancelled.is_set,
            )
            movie_bytes: int | None = None
            if manifest.rendered_count >= (2 if manifest.cancelled else 1):
                movie_bytes = _encode_all(manifest, out_dir, plan, plan.extras)
        except BaseException:
            status = "cancelled" if ctx.cancelled.is_set() else "failed"
            _update_row(engine, render_id, status=status)
            raise

        _update_row(
            engine,
            render_id,
            status="cancelled" if manifest.cancelled else "succeeded",
            frame_count=manifest.rendered_count,
            movie_bytes=movie_bytes,
        )
        return {"render_id": render_id}

    job_id = await jobs.submit("timelapse", json.loads(params_json), runner)
    return TimelapseCreated(job_id=job_id, render_id=render_id)


def _dataset_has_product(dataset_id: str, key: str) -> bool:
    try:
        get_product(dataset_id, key)
        return True
    except KeyError:
        return False


def preflight(req: PreflightRequest, cache: diskcache.Cache) -> PreflightOut:
    """Per-window availability strip (decision 11): scene counts + the native cap.

    Collection aggregates only (``size()`` per window over the source ladder) — no
    pixel stats — so it answers in seconds and is briefly cached. Scene-level cloud
    metadata (``mean_cloud``) is not populated in this pass (it needs per-source raw
    -collection property plumbing); the strip's load-bearing signal is scene
    presence / empty spans, which is fully delivered.
    """
    from openearth.ee.client import ee_call
    from openearth.providers import get_collection

    resolve_catalog(req.dataset, req.product)  # 404 unknown / 422 builder product
    roi = req.roi.to_domain()
    bbox = roi if isinstance(roi, BBox) else roi.bounds
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

    key = cache_key(
        "timelapse_preflight",
        dataset=req.dataset,
        product=req.product,
        roi=roi_key_part(roi),
        start=req.dates.start.isoformat(),
        end=req.dates.end.isoformat(),
        step=req.step.model_dump(),
        fallback=req.fallback_source,
    )
    cached = cache.get(key)
    if cached is not None:
        return PreflightOut.model_validate(cached)

    sources = [req.dataset]
    if req.fallback_source and req.dataset != "hls" and _dataset_has_product("hls", req.product):
        sources.append("hls")

    out_windows: list[PreflightWindowOut] = []
    empty = 0
    for window in windows:
        count, used = 0, req.dataset
        for src in sources:
            try:
                n = int(
                    ee_call(
                        get_collection(req.product, roi, window.start, window.end, src)
                        .size()
                        .getInfo
                    )
                    or 0
                )
            except Exception:  # a broken window is "no data here", not a 500
                n = 0
            if n > 0:
                count, used = n, src
                break
        if count == 0:
            empty += 1
        out_windows.append(
            PreflightWindowOut(
                start=window.start,
                end=window.end,
                label=window.label,
                scene_count=count,
                mean_cloud=None,
                source=used,
            )
        )

    out = PreflightOut(
        windows=out_windows,
        frame_count=len(windows) - empty,
        empty_count=empty,
        native_max_dim=native_max_dim(bbox, req.dataset),
    )
    cache.set(key, out.model_dump(mode="json"), expire=6 * 3600)
    return out


def _insert_running_row(
    engine: Engine,
    render_id: str,
    title: str,
    req: TimelapseRequest,
    params_json: str,
    roi_json: str,
    fps: int,
    fmt: str,
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
        fps=fps,
        format=fmt,
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
    # draft / preset / crops are surfaced from params_json (no dedicated columns).
    draft = False
    preset: str | None = None
    crops: list[str] = []
    try:
        params = json.loads(row.params_json)
        draft = bool(params.get("draft", False))
        preset = params.get("preset")
        crops = list(params.get("extras", {}).get("crops", []))
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
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
        draft=draft,
        preset=preset,
        crops=crops,
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


def update_render(engine: Engine, render_id: str, title: str) -> RenderOut:
    """Rename a gallery render. Allowed while running — the title is pure metadata."""
    cleaned = title.strip()
    if not cleaned:
        raise HTTPException(422, "Title must not be blank.")
    with Session(engine) as session:
        row = _require_render(session, render_id)
        row.title = cleaned
        row.updated_at = utcnow_iso()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _render_out(row)


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


def still_response(settings: Settings, render_id: str, index: int) -> FileResponse:
    """Download one frame as a full-resolution still (attachment, not inline)."""
    path = _render_dir(settings, render_id) / f"frame_{index:04d}.png"
    if not path.exists():
        raise HTTPException(404, f"No frame {index} in render {render_id!r}.")
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"{render_id}_still_{index:04d}.png",
        headers={"Cache-Control": "public, max-age=31536000"},
    )


def download_response(
    engine: Engine, settings: Settings, render_id: str, variant: str | None = None
) -> FileResponse:
    with Session(engine) as session:
        row = _require_render(session, render_id)
    # A cancelled *partial* with an encoded movie is downloadable too.
    if row.status not in ("succeeded", "cancelled"):
        raise HTTPException(
            409, f"Render is not finished (status={row.status!r}); watch its events."
        )

    suffix = "" if variant is None else f"_{variant.replace(':', '_')}"
    movie = _render_dir(settings, render_id) / f"movie{suffix}.{row.format}"
    if not movie.exists():
        if variant is not None:
            raise HTTPException(404, f"No {variant} crop was encoded for this render.")
        raise HTTPException(410, "The rendered movie was removed; re-run the timelapse.")

    params = json.loads(row.params_json)
    dates = params.get("dates", {})
    download_name = (
        f"{row.dataset}_{row.product}_{dates.get('start')}_{dates.get('end')}{suffix}.{row.format}"
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
