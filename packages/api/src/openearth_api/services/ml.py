"""ML tier scan — an onnxruntime U-Net that proposes candidate scenes for review.

Serves the model trained in ``packages/ml`` via **onnxruntime only** (never torch).
The session + manifest load lazily, so ``create_app()`` stays model-free at
creation and a missing model file is a ``503`` at submit — not an import error.
ML detections are written **off-loop** (own Session, worker thread) exactly like
the physics analyze runner, and carry **single-pass Q** (no MC → ``q_sigma`` null)
so they are magnitude-comparable in the same feed. The model is a candidate ranker
feeding human review — never an autonomous detector.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from fastapi import HTTPException
from numpy.typing import NDArray
from sqlmodel import Session

from openearth.methane.channels import (
    ChannelStats,
    build_channels,
    candidates_from_prob,
    normalize,
    pad_to_multiple,
    unpad,
)
from openearth.methane.conversion import (
    delta_omega_to_xch4_ppb,
    invert_fractional_signal,
    load_lut,
)
from openearth.methane.ime import emission_over_mask
from openearth.methane.plume import mask_outline_geojson
from openearth.methane.retrieval import RetrievalChip, check_chip_bbox, fetch_chip
from openearth.methane.scenes import S2Scene, list_scenes, pick_reference
from openearth.methane.wind import sample_wind_at
from openearth.settings import Settings
from openearth_api.models import Detection, utcnow_iso
from openearth_api.schemas import JobCreated, MlScanRequest, MlStatusOut
from openearth_api.services.methane import (
    _detections_dir,
    _overlay_bounds,
    _resolve_bbox,
    derive_physics_agreement,
)

# Nominal 10 m wind σ (m/s): recorded on the row but unused by the single-pass
# point estimate (only the MC budget, a physics-tier feature, consumes it).
_SIGMA_U10 = 1.5

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _model_paths(settings: Settings) -> tuple[Path, Path]:
    onnx = settings.ml_model_path or (settings.data_dir / "ml" / "models" / "plume_unet_v1.onnx")
    return onnx, onnx.with_suffix(".json")


def load_model(settings: Settings) -> dict[str, Any]:
    """Lazy singleton ORT session + manifest + stats. 503 if the model is absent."""
    onnx, manifest_path = _model_paths(settings)
    if not onnx.exists() or not manifest_path.exists():
        raise HTTPException(503, "ML model not installed — see docs/phase5-stage4b-handoff.md")
    with _lock:
        if not _cache:
            import onnxruntime as ort

            manifest = json.loads(manifest_path.read_text())
            cs = manifest["channel_stats"]
            _cache.update(
                session=ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"]),
                manifest=manifest,
                stats=ChannelStats(tuple(cs["channels"]), tuple(cs["median"]), tuple(cs["mad"])),
            )
    return _cache


def ml_status(settings: Settings) -> MlStatusOut:
    """Model availability — never raises (the app works with no model present)."""
    try:
        manifest = load_model(settings)["manifest"]
    except HTTPException:
        return MlStatusOut(model_loaded=False)
    return MlStatusOut(
        model_loaded=True,
        model_version=manifest["model_version"],
        latency_ms_p50=manifest.get("latency_ms_p50"),
    )


def _num(value: float | None) -> float | None:
    return None if value is None or not np.isfinite(value) else float(value)


def _forward(session: Any, x_hw5: NDArray[np.float32]) -> NDArray[np.float32]:
    """Serve path: pad → NCHW → ORT logits → sigmoid → unpad to native size."""
    padded, spec = pad_to_multiple(x_hw5)
    nchw = np.ascontiguousarray(padded.transpose(2, 0, 1)[None]).astype(np.float32)
    logits = session.run(None, {session.get_inputs()[0].name: nchw})[0]
    prob = 1.0 / (1.0 + np.exp(-logits[0, 0]))
    return unpad(prob.astype(np.float32), spec)


def _grid_json(grid: Any) -> str:
    return json.dumps(
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


def _persist_ml_detection(
    engine: Any,
    settings: Settings,
    site_id: int,
    scene: S2Scene,
    reference: S2Scene,
    target: RetrievalChip,
    prob: NDArray[np.float32],
    mask: NDArray[np.bool_],
    delta_r: NDArray[np.float64],
    delta_omega: NDArray[np.float64],
    xch4: NDArray[np.float64],
    emission: Any,
    score: float,
    n_candidates: int,
    model_version: str,
    disagreement: str,
) -> str:
    det_id = uuid4().hex
    _detections_dir(settings).mkdir(parents=True, exist_ok=True)
    array_path = _detections_dir(settings) / f"{det_id}.npz"
    rgb = np.stack([target.bands["B4"], target.bands["B3"], target.bands["B2"]], axis=-1).astype(
        np.float32
    )
    tmp = array_path.with_name(array_path.name + ".tmp")
    with tmp.open("wb") as fh:
        np.savez_compressed(
            fh,
            delta_r=delta_r.astype(np.float32),
            delta_omega=delta_omega.astype(np.float32),
            xch4_ppb=np.asarray(xch4, dtype=np.float32),
            mask=mask.astype(np.uint8),
            prob=prob.astype(np.float32),
            rgb=rgb,
            grid=_grid_json(target.grid),
        )
    tmp.rename(array_path)

    result_json = {
        "score": round(float(score), 4),
        "model_version": model_version,
        "n_candidates": n_candidates,
        # Scan-time snapshot of physics agreement (historical). Display uses the
        # read-time-derived DetectionOut.physics_agreement, so old rows stay correct.
        "disagreement": disagreement,
        "flags": [],
        "review": "ML candidate — requires review; not an autonomous detection.",
        # Grid corners so the detail's overlay places on the map and the
        # validation cross-match can locate the candidate (same key physics writes).
        "overlay_bounds": _overlay_bounds(target.grid),
    }
    now = utcnow_iso()
    row = Detection(
        id=det_id,
        site_id=site_id,
        source="ml",
        status="candidate",
        method="ml_unet",
        scene_id=scene.scene_id,
        scene_time_utc=scene.time.isoformat(),
        ref_scene_id=reference.scene_id,
        q_kg_h=_num(emission.q_kg_h),
        q_sigma_kg_h=None,  # single-pass: no uncertainty budget
        xch4_max_ppb=_num(float(np.nanmax(xch4[mask])) if mask.any() else None),
        ime_kg=_num(emission.ime_kg),
        u10_ms=_num(emission.u10_ms),
        wind_from_deg=_num(emission.wind_from_deg),
        params_json=json.dumps({"model_version": model_version}),
        result_json=json.dumps(result_json),
        mask_geojson=json.dumps(mask_outline_geojson(mask, target.grid)),
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


def _scan_one_scene(
    site_id: int,
    scene: S2Scene,
    candidates: list[S2Scene],
    bbox: Any,
    model: dict[str, Any],
    settings: Settings,
    engine: Any,
) -> str | None:
    """One scene → a detection row if the model proposes ≥1 candidate, else None."""
    reference = pick_reference(scene, candidates)
    if reference is None:
        return None
    target = fetch_chip(scene, bbox)
    ref_chip = fetch_chip(reference, bbox)
    raw = build_channels(target, ref_chip)  # (H, W, 5); channel 0 = mbmp ΔR
    prob = _forward(model["session"], normalize(raw, model["stats"]))
    manifest = model["manifest"]
    cands = candidates_from_prob(
        prob, threshold=manifest["threshold"], min_px=manifest["min_px"], grid=target.grid
    )
    if not cands:
        return None
    mask = np.zeros(prob.shape, dtype=bool)
    for c in cands:
        mask |= c.mask

    # Single-pass Q over the ML footprint (no MC).
    delta_r = raw[..., 0].astype(np.float64)
    delta_omega = invert_fractional_signal(delta_r, load_lut(), scene.spacecraft, scene.amf)
    xch4 = np.asarray(delta_omega_to_xch4_ppb(delta_omega), dtype=np.float64)
    wind = sample_wind_at(bbox, scene.time)
    emission = emission_over_mask(delta_omega, target.grid, mask, wind, _SIGMA_U10)

    return _persist_ml_detection(
        engine,
        settings,
        site_id,
        scene,
        reference,
        target,
        prob,
        mask,
        delta_r,
        delta_omega,
        xch4,
        emission,
        score=max(c.max_prob for c in cands),
        n_candidates=len(cands),
        model_version=manifest["model_version"],
        disagreement=derive_physics_agreement(engine, site_id, scene.scene_id),
    )


async def submit_ml_scan(
    req: MlScanRequest, jobs: Any, engine: Any, settings: Settings
) -> JobCreated:
    """Validate the site + model, then submit the ``methane_ml_scan`` job."""
    with Session(engine) as session:
        bbox = _resolve_bbox(session, req.site_id, req.roi)  # 404 if the site is unknown
    try:
        check_chip_bbox(bbox)  # fail at submit, not partway through the scan
    except ValueError as exc:
        raise HTTPException(
            422, f"{exc} At 20 m the analysis area is limited to ~20 km per side."
        ) from exc
    load_model(settings)  # 503 at submit if the model is not installed

    def runner(ctx: Any) -> dict[str, Any]:
        model = load_model(settings)
        scenes = list_scenes(bbox, req.start, req.end)
        if req.max_scenes:
            scenes = scenes[: req.max_scenes]
        det_ids: list[str] = []
        for i, scene in enumerate(scenes):
            if ctx.cancelled.is_set():
                break
            det_id = _scan_one_scene(req.site_id, scene, scenes, bbox, model, settings, engine)
            if det_id is not None:
                det_ids.append(det_id)
            label = f"scanned {i + 1}/{len(scenes)}, {len(det_ids)} hit(s)"
            ctx.progress(i + 1, len(scenes), label)
        return {"detection_ids": det_ids}

    job_id = await jobs.submit("methane_ml_scan", req.model_dump(mode="json"), runner)
    return JobCreated(job_id=job_id)
