"""Evaluation: scene-level F1 (the roadmap gate), pixel metrics, physics baseline.

The gate is a *scene-level* decision made the same way for the model and the
physics baseline: a scene is predicted positive iff a connected component of at
least ``PLUME_MIN_PX`` survives — for the model, of ``prob ≥ 0.5``; for the
baseline, of ``−ΔR_MBMP ≥ k·σ`` via the physics tier's own ``detect_plume``.
Truth is a non-empty CH4Net mask. The model is run through the *serve* path
(``pad_to_multiple`` → forward → ``unpad``) so eval and scan agree.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from numpy.typing import NDArray

from openearth.ee.pixels import GridSpec
from openearth.methane.channels import (
    PAD_MULTIPLE,
    ChannelStats,
    candidates_from_prob,
    normalize,
    pad_to_multiple,
    unpad,
)
from openearth.methane.plume import detect_plume
from openearth_ml.data import ChipRef

PLUME_MIN_PX = 5  # == plume.detect_plume default min_area_px
MODEL_THRESHOLD = 0.5
BASELINE_K_SIGMA = 2.0  # pipeline-default k_sigma


@dataclass(frozen=True)
class SceneMetrics:
    f1: float
    precision: float
    recall: float
    tp: int
    fp: int
    fn: int
    tn: int


def scene_metrics(truth: list[bool], pred: list[bool]) -> SceneMetrics:
    tp = sum(t and p for t, p in zip(truth, pred, strict=True))
    fp = sum((not t) and p for t, p in zip(truth, pred, strict=True))
    fn = sum(t and (not p) for t, p in zip(truth, pred, strict=True))
    tn = sum((not t) and (not p) for t, p in zip(truth, pred, strict=True))
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return SceneMetrics(f1, prec, rec, tp, fp, fn, tn)


def _dummy_grid(shape: tuple[int, int]) -> GridSpec:
    # detect_plume only uses the grid for area_m2, which the scene decision ignores.
    return GridSpec(x0=0.0, y0=0.0, xscale=1.0, yscale=1.0, width=shape[1], height=shape[0])


def model_prob(model: torch.nn.Module, stats: ChannelStats, ref: ChipRef, device: str) -> NDArray:
    """Run the model on one chip through the serve path → native-size prob map."""
    raw = np.load(ref.path)["channels"]
    padded, spec = pad_to_multiple(normalize(raw, stats), PAD_MULTIPLE)
    x = torch.from_numpy(np.ascontiguousarray(padded)).permute(2, 0, 1)[None].to(device)
    model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model(x))[0, 0].cpu().numpy()
    return unpad(prob.astype(np.float32), spec)


def baseline_scene_positive(raw_channels: NDArray, k_sigma: float = BASELINE_K_SIGMA) -> bool:
    """Physics baseline: detect_plume on −ΔR_MBMP (channel 0), same scene rule."""
    field = -raw_channels[..., 0].astype(np.float64)
    mask = detect_plume(
        field, _dummy_grid(field.shape[:2]), k_sigma=k_sigma, min_area_px=PLUME_MIN_PX
    ).mask
    return bool(mask.any())


def _iou(a: NDArray[np.bool_], b: NDArray[np.bool_]) -> float:
    inter = int((a & b).sum())
    union = int((a | b).sum())
    return inter / union if union else 1.0


def evaluate(
    model: torch.nn.Module, stats: ChannelStats, refs: list[ChipRef], device: str
) -> dict[str, object]:
    """Scene-level F1 for model + physics baseline, plus pixel IoU on true positives."""
    truth: list[bool] = []
    model_pred: list[bool] = []
    base_pred: list[bool] = []
    ious: list[float] = []
    for ref in refs:
        raw = np.load(ref.path)["channels"]
        truth_mask = np.load(ref.path)["mask"].astype(bool)
        is_pos = bool(truth_mask.any())
        prob = model_prob(model, stats, ref, device)
        cands = candidates_from_prob(prob, threshold=MODEL_THRESHOLD, min_px=PLUME_MIN_PX)
        truth.append(is_pos)
        model_pred.append(len(cands) > 0)
        base_pred.append(baseline_scene_positive(raw))
        if is_pos and cands:
            pred_mask = prob >= MODEL_THRESHOLD
            ious.append(_iou(pred_mask, truth_mask))
    m = scene_metrics(truth, model_pred)
    b = scene_metrics(truth, base_pred)
    return {
        "n": len(refs),
        "n_positive": int(sum(truth)),
        "model": vars(m),
        "baseline": vars(b),
        "pixel_iou_tp_mean": float(np.mean(ious)) if ious else None,
    }
