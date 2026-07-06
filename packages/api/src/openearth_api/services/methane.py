"""Methane Lab services: sites CRUD, scene search, the analyze job, and the
detection feed + artifacts.

The core detection entry points (``analyze``, ``list_scenes``) are imported by
name so offline tests fake them at this module level. The analyze runner writes
its own ``detections`` row from the worker thread via a fresh short-lived
``Session`` (WAL + busy_timeout handle the concurrency) — the manager-owned
``jobs`` table stays event-loop-only, but a completed detection's natural owner
is its runner.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from openearth.catalog.presets import METHANE_SITES
from openearth.geometry import BBox
from openearth.methane.conversion import load_lut
from openearth.methane.detect import analyze
from openearth.methane.ime import McParams
from openearth.methane.plume import mask_outline_geojson
from openearth.methane.scenes import list_scenes
from openearth.methane.tropomi import screen_region
from openearth.methane.validation import ReferenceEvent as CoreReferenceEvent
from openearth.methane.validation import match_detection, parse_events
from openearth_api.cache import cache_key
from openearth_api.models import Detection, ReferenceEvent, Site, utcnow_iso
from openearth_api.schemas import (
    AnalyzeRequest,
    DetectionDetailOut,
    DetectionOut,
    DetectionPatch,
    JobCreated,
    ReferenceEventOut,
    SceneInfoOut,
    ScreeningRequest,
    SiteIn,
    SiteOut,
    SitePatch,
    ValidationImportOut,
    ValidationOut,
)
from openearth_api.services.methane_render import render_overlay_png

if TYPE_CHECKING:
    from pathlib import Path

    import diskcache
    from sqlalchemy import Engine

    from openearth.methane.detect import DetectionResult
    from openearth.settings import Settings
    from openearth_api.jobs import JobContext, JobManager

# Cloud fraction at or below which a scene can serve as an MBMP reference.
_REF_MAX_CLOUD = 30.0


# ── NaN-safe JSON helpers ──


def _num(value: float | None) -> float | None:
    """Coerce a possibly-NaN float to a JSON-safe float or None."""
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f


def _clean(obj: Any) -> Any:
    """Recursively replace NaN/inf floats with None (FastAPI emits invalid JSON otherwise)."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


# ── Sites ──


def _site_out(row: Site) -> SiteOut:
    assert row.id is not None
    from openearth_api.schemas import BBoxIn

    return SiteOut(
        id=row.id,
        name=row.name,
        bbox=BBoxIn(west=row.west, south=row.south, east=row.east, north=row.north),
        date_hint_start=row.date_hint_start,  # type: ignore[arg-type]
        date_hint_end=row.date_hint_end,  # type: ignore[arg-type]
        notes=row.notes,
        created_at=row.created_at,
    )


def seed_sites(engine: Engine) -> None:
    """Insert the 7 built-in methane sites if the table is empty (idempotent)."""
    with Session(engine) as session:
        if session.exec(select(Site).limit(1)).first() is not None:
            return
        for preset in METHANE_SITES.values():
            hint = preset.date_hint
            session.add(
                Site(
                    name=preset.name.removeprefix("CH4: "),
                    west=preset.bbox.west,
                    south=preset.bbox.south,
                    east=preset.bbox.east,
                    north=preset.bbox.north,
                    date_hint_start=hint[0] if hint else None,
                    date_hint_end=hint[1] if hint else None,
                    notes=None,
                    created_at=utcnow_iso(),
                )
            )
        session.commit()


def list_sites(engine: Engine) -> list[SiteOut]:
    with Session(engine) as session:
        return [_site_out(r) for r in session.exec(select(Site).order_by(Site.name)).all()]


def create_site(body: SiteIn, engine: Engine) -> SiteOut:
    body.bbox.to_domain()  # geometry sanity → 422
    row = Site(
        name=body.name,
        west=body.bbox.west,
        south=body.bbox.south,
        east=body.bbox.east,
        north=body.bbox.north,
        date_hint_start=body.date_hint_start.isoformat() if body.date_hint_start else None,
        date_hint_end=body.date_hint_end.isoformat() if body.date_hint_end else None,
        notes=body.notes,
        created_at=utcnow_iso(),
    )
    with Session(engine) as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(409, f"Site {body.name!r} already exists.") from exc
        session.refresh(row)
        return _site_out(row)


def _require_site(session: Session, site_id: int) -> Site:
    row = session.get(Site, site_id)
    if row is None:
        raise HTTPException(404, f"No site {site_id}.")
    return row


def patch_site(site_id: int, body: SitePatch, engine: Engine) -> SiteOut:
    fields = body.model_dump(exclude_unset=True)
    with Session(engine) as session:
        row = _require_site(session, site_id)
        if "name" in fields and fields["name"] is not None:
            row.name = fields["name"]
        if "bbox" in fields and fields["bbox"] is not None:
            bbox = body.bbox
            assert bbox is not None
            bbox.to_domain()
            row.west, row.south, row.east, row.north = bbox.west, bbox.south, bbox.east, bbox.north
        if "date_hint_start" in fields:
            row.date_hint_start = body.date_hint_start.isoformat() if body.date_hint_start else None
        if "date_hint_end" in fields:
            row.date_hint_end = body.date_hint_end.isoformat() if body.date_hint_end else None
        if "notes" in fields:
            row.notes = body.notes
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "A site with that name already exists.") from exc
        session.refresh(row)
        return _site_out(row)


def delete_site(site_id: int, engine: Engine) -> None:
    with Session(engine) as session:
        row = _require_site(session, site_id)
        session.delete(row)
        session.commit()


def _resolve_bbox(session: Session, site_id: int | None, roi: Any) -> BBox:
    """Locate the analysis bbox from a site id or an inline ROI (exactly one)."""
    if (site_id is None) == (roi is None):
        raise HTTPException(422, "Provide exactly one of 'site_id' or 'roi'.")
    if site_id is not None:
        row = _require_site(session, site_id)
        return BBox(row.west, row.south, row.east, row.north)
    return roi.to_domain()


# ── Scenes ──


def list_scenes_for(
    engine: Engine, site_id: int | None, roi: Any, start: str, end: str, max_cloud: float
) -> list[SceneInfoOut]:
    with Session(engine) as session:
        bbox = _resolve_bbox(session, site_id, roi)
    scenes = list_scenes(bbox, start, end, max_cloud=max_cloud)
    return [
        SceneInfoOut(
            scene_id=s.scene_id,
            time=s.time,
            cloud_pct=s.cloud_pct,
            relative_orbit=s.relative_orbit,
            spacecraft=s.spacecraft,
            sun_zenith_deg=s.sun_zenith_deg,
            view_zenith_deg=s.view_zenith_deg,
            amf=s.amf,
            ref_ok=s.cloud_pct <= _REF_MAX_CLOUD,
        )
        for s in scenes
    ]


# ── Analyze job + detection persistence ──


def _overlay_bounds(grid: Any) -> list[list[float]]:
    west, north = grid.x0, grid.y0
    east = grid.x0 + grid.width * grid.xscale
    south = grid.y0 - grid.height * grid.yscale
    return [[west, north], [east, north], [east, south], [west, south]]


def _detections_dir(settings: Settings) -> Path:
    return settings.data_dir / "detections"


def _write_npz(path: Path, result: DetectionResult, params: dict[str, Any]) -> None:
    grid = result.grid
    grid_json = json.dumps(
        {
            "x0": grid.x0,
            "y0": grid.y0,
            "xscale": grid.xscale,
            "yscale": grid.yscale,
            "width": grid.width,
            "height": grid.height,
            "crs": grid.crs,
        }
    )
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        np.savez_compressed(
            fh,
            delta_r=result.delta_r.astype(np.float32),
            delta_omega=result.delta_omega.astype(np.float32),
            xch4_ppb=result.xch4_ppb.astype(np.float32),
            mask=result.plume.mask.astype(np.uint8),
            rgb=result.rgb.astype(np.float32),
            grid=grid_json,
            lut_version=load_lut().version,
            params=json.dumps(params, default=str),
        )
    tmp.rename(path)


def _xch4_max(result: DetectionResult) -> float | None:
    values = result.xch4_ppb[result.plume.mask] if result.plume.n_pixels > 0 else result.xch4_ppb
    finite = values[np.isfinite(values)]
    return float(finite.max()) if finite.size else None


def _result_payload(result: DetectionResult, xch4_max: float | None) -> dict[str, Any]:
    em = result.emission
    return _clean(
        {
            "method": result.method,
            "flags": result.flags,
            "percentiles": em.percentiles,
            "histogram": em.histogram,
            "calibration": result.calibration,
            "ime_kg": em.ime_kg,
            "l_m": em.l_m,
            "u_eff_ms": em.u_eff_ms,
            "u10_ms": em.u10_ms,
            "sigma_u10_ms": em.sigma_u10_ms,
            "wind_from_deg": em.wind_from_deg,
            "xch4_max_ppb": xch4_max,
            "n_mc": em.n_mc,
            # The plume footprint is thresholded on the FROZEN mask-LUT ΔΩ (invariant to
            # reporting-LUT recalibration); the mask σ and the reporting-ΔΩ noise σ are
            # distinct populations and must not be conflated.
            "mask_domain": "frozen_lut_delta_omega",
            "sigma_mask": result.plume.sigma,
            "sigma_noise_delta_omega": em.sigma_noise_delta_omega,
            "plume": {
                "n_pixels": result.plume.n_pixels,
                "area_m2": result.plume.area_m2,
                "k_sigma": result.plume.k_sigma,
                "sigma": result.plume.sigma,
            },
            "target_scene_id": result.target.scene_id,
            "reference_scene_id": result.reference.scene_id if result.reference else None,
            "spacecraft": result.target.spacecraft,
            "amf": result.target.amf,
            "overlay_bounds": _overlay_bounds(result.grid),
            "lut_version": load_lut().version,
        }
    )


def persist_detection(
    engine: Engine,
    settings: Settings,
    req: AnalyzeRequest,
    site_id: int | None,
    result: DetectionResult,
) -> str:
    """Write the npz artifact then the detection row (own Session, worker thread)."""
    det_id = uuid4().hex
    params = req.model_dump()
    params["mask_domain"] = "frozen_lut_delta_omega"  # footprint from the frozen mask LUT
    _detections_dir(settings).mkdir(parents=True, exist_ok=True)
    array_path = _detections_dir(settings) / f"{det_id}.npz"
    _write_npz(array_path, result, params)

    xch4_max = _xch4_max(result)
    mask_geojson = (
        json.dumps(mask_outline_geojson(result.plume.mask, result.grid))
        if result.plume.n_pixels > 0
        else None
    )
    now = utcnow_iso()
    row = Detection(
        id=det_id,
        site_id=site_id,
        source="physics",
        status="candidate",
        method=result.method,
        scene_id=result.target.scene_id,
        scene_time_utc=result.target.time.isoformat(),
        ref_scene_id=result.reference.scene_id if result.reference else None,
        q_kg_h=_num(result.emission.q_kg_h),
        q_sigma_kg_h=_num(result.emission.q_sigma_kg_h),
        xch4_max_ppb=_num(xch4_max),
        ime_kg=_num(result.emission.ime_kg),
        u10_ms=_num(result.emission.u10_ms),
        wind_from_deg=_num(result.emission.wind_from_deg),
        params_json=json.dumps(params, default=str),
        result_json=json.dumps(_result_payload(result, xch4_max)),
        mask_geojson=mask_geojson,
        array_path=f"detections/{det_id}.npz",
        notes=None,
        validation_json=None,
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()
    return det_id


async def submit_analyze(
    req: AnalyzeRequest, jobs: JobManager, engine: Engine, settings: Settings
) -> JobCreated:
    """Validate the request, then submit the ``methane_analyze`` job."""
    with Session(engine) as session:
        bbox = _resolve_bbox(session, req.site_id, req.roi)  # 404/422 at request time
    source_lonlat = tuple(req.source_lonlat) if req.source_lonlat is not None else None

    def runner(ctx: JobContext) -> dict[str, Any]:
        result = analyze(
            bbox,
            req.target_scene_id,
            reference_scene_id=req.reference_scene_id,
            method=req.method,
            k_sigma=req.k_sigma,
            min_area_px=req.min_area_px,
            source_lonlat=source_lonlat,  # type: ignore[arg-type]
            mc=McParams(seed=req.seed),
            on_progress=lambda step, total, label: ctx.progress(step, total, label),
            cancel=ctx.cancelled,
        )
        det_id = persist_detection(engine, settings, req, req.site_id, result)
        return {"detection_id": det_id}

    params = req.model_dump()
    job_id = await jobs.submit("methane_analyze", params, runner)
    return JobCreated(job_id=job_id)


# ── Detection feed + detail ──


def _flags_of(result_json: str) -> list[str]:
    parsed = json.loads(result_json)
    flags = parsed.get("flags", [])
    return list(flags) if isinstance(flags, list) else []


def _detection_out(row: Detection) -> DetectionOut:
    return DetectionOut(
        id=row.id,
        site_id=row.site_id,
        source=row.source,
        status=row.status,  # type: ignore[arg-type]
        method=row.method,
        scene_id=row.scene_id,
        scene_time_utc=row.scene_time_utc,
        q_kg_h=row.q_kg_h,
        q_sigma_kg_h=row.q_sigma_kg_h,
        xch4_max_ppb=row.xch4_max_ppb,
        u10_ms=row.u10_ms,
        wind_from_deg=row.wind_from_deg,
        flags=_flags_of(row.result_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_detections(
    engine: Engine, site_id: int | None, status: str | None, limit: int, offset: int
) -> list[DetectionOut]:
    with Session(engine) as session:
        query = select(Detection)
        if site_id is not None:
            query = query.where(Detection.site_id == site_id)
        if status is not None:
            query = query.where(Detection.status == status)
        query = query.order_by(Detection.created_at.desc()).limit(limit).offset(offset)  # type: ignore[attr-defined]
        return [_detection_out(r) for r in session.exec(query).all()]


def _require_detection(session: Session, det_id: str) -> Detection:
    row = session.get(Detection, det_id)
    if row is None:
        raise HTTPException(404, f"No detection {det_id!r}.")
    return row


def get_detection_detail(engine: Engine, det_id: str) -> DetectionDetailOut:
    with Session(engine) as session:
        row = _require_detection(session, det_id)
        result = json.loads(row.result_json)
        return DetectionDetailOut(
            **_detection_out(row).model_dump(),
            reference_scene_id=row.ref_scene_id,
            ime_kg=row.ime_kg,
            notes=row.notes,
            result=result,
            params=json.loads(row.params_json),
            mask_geojson=json.loads(row.mask_geojson) if row.mask_geojson else None,
            overlay_bounds=result.get("overlay_bounds"),
            validation=json.loads(row.validation_json) if row.validation_json else None,
        )


def patch_detection(engine: Engine, det_id: str, body: DetectionPatch) -> DetectionDetailOut:
    fields = body.model_dump(exclude_unset=True)
    with Session(engine) as session:
        row = _require_detection(session, det_id)
        if "status" in fields and fields["status"] is not None:
            row.status = fields["status"]
        if "notes" in fields:
            row.notes = body.notes
        row.updated_at = utcnow_iso()
        session.add(row)
        session.commit()
    return get_detection_detail(engine, det_id)


def delete_detection(engine: Engine, settings: Settings, det_id: str) -> None:
    with Session(engine) as session:
        row = _require_detection(session, det_id)
        array_path = settings.data_dir / row.array_path
        session.delete(row)
        session.commit()
    array_path.unlink(missing_ok=True)


def detection_array_path(engine: Engine, settings: Settings, det_id: str) -> Path:
    with Session(engine) as session:
        row = _require_detection(session, det_id)
        path = settings.data_dir / row.array_path
    if not path.exists():
        raise HTTPException(410, "The detection array was removed; re-run the analysis.")
    return path


def overlay_png(
    engine: Engine,
    settings: Settings,
    cache: diskcache.Cache,
    det_id: str,
    vmin: float | None,
    vmax: float | None,
) -> bytes:
    """Render (and cache) the detection's overlay PNG. Recomputable → evictable."""
    path = detection_array_path(engine, settings, det_id)
    key = cache_key("methane_overlay", id=det_id, vmin=vmin, vmax=vmax)
    cached = cache.get(key)
    if cached is not None:
        return bytes(cached)
    png = render_overlay_png(path, vmin, vmax)
    cache.set(key, png)
    return png


# ── S5P screening job ──


async def submit_screening(req: ScreeningRequest, jobs: JobManager) -> JobCreated:
    """Validate the bbox, then submit the ``methane_screening`` job.

    The hotspot list (≤ top_n) fits comfortably in the job's ``result_json``;
    there is no separate artifact.
    """
    bbox = req.roi.to_domain()  # 422 on malformed geometry

    def runner(ctx: JobContext) -> dict[str, Any]:
        hotspots = screen_region(
            bbox,
            req.start,
            req.end,
            background_days=req.background_days,
            cell_deg=req.cell_deg,
            sigma_thresh=req.sigma_thresh,
            top_n=req.top_n,
            on_progress=lambda i, n, label: ctx.progress(i, n, label),
            cancel=ctx.cancelled,
        )
        return {
            "hotspots": [
                {
                    "lat": h.lat,
                    "lon": h.lon,
                    "mean_enh_ppb": h.mean_enh_ppb,
                    "max_enh_ppb": h.max_enh_ppb,
                    "score": h.score,
                    "weeks_flagged": h.weeks_flagged,
                    "weeks_observed": h.weeks_observed,
                }
                for h in hotspots
            ]
        }

    job_id = await jobs.submit("methane_screening", req.model_dump(mode="json"), runner)
    return JobCreated(job_id=job_id)


# ── Validation: reference-event import + cross-match ──


def _count_records(data: bytes, fmt: str) -> int:
    """Count parseable-shape records so ``skipped = total − imported``."""
    if fmt == "csv":
        return sum(1 for _ in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    doc = json.loads(data.decode("utf-8"))
    features = doc.get("features", []) if isinstance(doc, dict) else []
    return sum(1 for f in features if (f.get("geometry") or {}).get("type") == "Point")


def import_events(engine: Engine, data: bytes, source: str, fmt: str) -> ValidationImportOut:
    if fmt not in ("csv", "geojson"):
        raise HTTPException(422, "fmt must be 'csv' or 'geojson'.")
    events = parse_events(data, fmt=fmt, source=source)  # type: ignore[arg-type]
    total = _count_records(data, fmt)
    now = utcnow_iso()
    with Session(engine) as session:
        for event in events:
            session.add(
                ReferenceEvent(
                    source=event.source,
                    event_time_utc=event.event_time_utc,
                    lat=event.lat,
                    lon=event.lon,
                    q_kg_h=event.q_kg_h,
                    q_sigma_kg_h=event.q_sigma_kg_h,
                    raw_json=json.dumps(event.raw, default=str),
                    imported_at=now,
                )
            )
        session.commit()
    return ValidationImportOut(imported=len(events), skipped=max(0, total - len(events)))


def list_events(engine: Engine) -> list[ReferenceEventOut]:
    with Session(engine) as session:
        rows = session.exec(
            select(ReferenceEvent).order_by(ReferenceEvent.event_time_utc.desc())  # type: ignore[attr-defined]
        ).all()
        return [
            ReferenceEventOut(
                id=r.id,  # type: ignore[arg-type]
                source=r.source,
                event_time_utc=r.event_time_utc,
                lat=r.lat,
                lon=r.lon,
                q_kg_h=r.q_kg_h,
                q_sigma_kg_h=r.q_sigma_kg_h,
                imported_at=r.imported_at,
            )
            for r in rows
        ]


def _detection_center(row: Detection) -> tuple[float, float]:
    """(lat, lon) of the detection from its stored overlay bounds (grid corners)."""
    bounds = json.loads(row.result_json).get("overlay_bounds")
    if not bounds:
        raise HTTPException(422, "Detection has no geometry to validate against.")
    lons = [c[0] for c in bounds]
    lats = [c[1] for c in bounds]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def validate_detection(engine: Engine, det_id: str) -> ValidationOut:
    """Cross-match a detection against all imported reference events; persist the verdict."""
    with Session(engine) as session:
        row = _require_detection(session, det_id)
        det_lat, det_lon = _detection_center(row)
        det_time = datetime.fromisoformat(row.scene_time_utc)
        event_rows = session.exec(select(ReferenceEvent)).all()
        events = [
            CoreReferenceEvent(
                source=e.source,
                event_time_utc=e.event_time_utc,
                lat=e.lat,
                lon=e.lon,
                q_kg_h=e.q_kg_h,
                q_sigma_kg_h=e.q_sigma_kg_h,
                raw={},
            )
            for e in event_rows
        ]
        verdict, matched_idx = match_detection(det_lat, det_lon, det_time, events)
        matched_ids = [e.id for i, e in enumerate(event_rows) if i in set(matched_idx) and e.id]
        result = ValidationOut(verdict=verdict, matched_event_ids=matched_ids)  # type: ignore[arg-type]
        row.validation_json = json.dumps(
            {"verdict": verdict, "matched_event_ids": matched_ids, "validated_at": utcnow_iso()}
        )
        row.updated_at = utcnow_iso()
        session.add(row)
        session.commit()
    return result
