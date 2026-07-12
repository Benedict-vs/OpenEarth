"""Evaluation: scene-level F1 with threshold/k sweeps and two truth sets.

The gate is a *scene-level* decision made the same way for the model and the physics
baseline: a scene is predicted positive iff a connected component of at least
``PLUME_MIN_PX`` survives — for the model, of ``prob ≥ threshold``; for the baseline,
of ``−ΔR_MBMP ≥ k·σ`` via the physics tier's own ``detect_plume``. Truth is a
non-empty CH4Net mask. The model runs through the *serve* path (``pad_to_multiple``
→ forward → ``unpad``) so eval and scan agree.

Fix 6: both sides get a full sweep (model over prob thresholds, baseline over k), so
the headline is not two arbitrary operating points. Fix 7: metrics are reported over
two truth sets — primary (quality-filtered labels) and secondary (all labels).
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
from openearth_ml.data import ChipRef, _chip_key

PLUME_MIN_PX = 5  # == plume.detect_plume default min_area_px
BASELINE_K_SIGMA = 2.0  # pipeline-default k_sigma (the fair, non-oracle baseline point)
MODEL_THRESHOLD_GRID = tuple(round(float(t), 2) for t in np.arange(0.05, 0.951, 0.05))
BASELINE_K_GRID = tuple(round(float(k), 2) for k in np.arange(1.0, 4.001, 0.25))


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


def _model_scene_pos(prob: NDArray, threshold: float) -> bool:
    return len(candidates_from_prob(prob, threshold=threshold, min_px=PLUME_MIN_PX)) > 0


def _baseline_field(ref: ChipRef) -> NDArray:
    return -np.load(ref.path)["channels"][..., 0].astype(np.float64)  # −ΔR_MBMP


def _baseline_scene_pos(field: NDArray, k_sigma: float) -> bool:
    mask = detect_plume(
        field, _dummy_grid(field.shape[:2]), k_sigma=k_sigma, min_area_px=PLUME_MIN_PX
    ).mask
    return bool(mask.any())


def baseline_scene_positive(raw_channels: NDArray, k_sigma: float = BASELINE_K_SIGMA) -> bool:
    """Physics baseline scene decision on −ΔR_MBMP (channel 0) — the same rule the eval uses."""
    return _baseline_scene_pos(-raw_channels[..., 0].astype(np.float64), k_sigma)


def _sweep(preds_by_param: dict[float, list[bool]], truth: list[bool]) -> list[dict]:
    rows = []
    for param, pred in preds_by_param.items():
        m = scene_metrics(truth, pred)
        rows.append({"param": param, "f1": m.f1, "precision": m.precision, "recall": m.recall})
    return rows


def select_threshold(
    probs: list[NDArray], truth: list[bool], thresholds: tuple[float, ...] = MODEL_THRESHOLD_GRID
) -> float:
    """Prob threshold maximising scene F1 (ties → lower threshold). Inner-val only."""
    best_t, best_f1 = thresholds[0], -1.0
    for t in thresholds:
        f1 = scene_metrics(truth, [_model_scene_pos(p, t) for p in probs]).f1
        if f1 > best_f1:
            best_t, best_f1 = t, f1
    return best_t


def _iou(a: NDArray[np.bool_], b: NDArray[np.bool_]) -> float:
    inter = int((a & b).sum())
    union = int((a | b).sum())
    return inter / union if union else 1.0


def evaluate(
    model: torch.nn.Module,
    stats: ChannelStats,
    refs: list[ChipRef],
    device: str,
    *,
    threshold: float = 0.5,
    primary_keys: set[str] | None = None,
) -> dict[str, object]:
    """Full fold eval: model at *threshold* and baseline at k = 2 over both truth sets,
    plus both sweeps and the baseline's eval-oracle best-k (an upper bound favouring it).

    *primary_keys* are the quality-filtered chip keys; when given, "primary" metrics
    drop the excluded (net-negative) positives from the truth. Absent → primary == all.
    """
    probs = [model_prob(model, stats, r, device) for r in refs]
    fields = [_baseline_field(r) for r in refs]
    truth = [bool(np.load(r.path)["mask"].any()) for r in refs]
    keep = [primary_keys is None or _chip_key(r) in primary_keys for r in refs]

    def subset(values: list) -> list:
        return [v for v, k in zip(values, keep, strict=True) if k]

    p_truth = subset(truth)
    model_pred_p = subset([_model_scene_pos(pr, threshold) for pr in probs])
    base_pred_p = subset([_baseline_scene_pos(f, BASELINE_K_SIGMA) for f in fields])

    # Sweeps (over the primary truth set — the citable numbers).
    model_sweep = _sweep(
        {t: subset([_model_scene_pos(pr, t) for pr in probs]) for t in MODEL_THRESHOLD_GRID},
        p_truth,
    )
    base_sweep = _sweep(
        {k: subset([_baseline_scene_pos(f, k) for f in fields]) for k in BASELINE_K_GRID}, p_truth
    )
    base_oracle = max(base_sweep, key=lambda r: r["f1"])

    # Pixel IoU on model true positives (primary set).
    ious = [
        _iou(pr >= threshold, np.load(r.path)["mask"].astype(bool))
        for pr, r, is_pos, k in zip(probs, refs, truth, keep, strict=True)
        if k and is_pos and _model_scene_pos(pr, threshold)
    ]

    return {
        "n": len(refs),
        "n_primary": len(p_truth),
        "n_positive": int(sum(truth)),
        "n_primary_positive": int(sum(p_truth)),
        "threshold": threshold,
        "model": vars(scene_metrics(p_truth, model_pred_p)),
        "model_all_labels": vars(
            scene_metrics(truth, [_model_scene_pos(pr, threshold) for pr in probs])
        ),
        "baseline_k2": vars(scene_metrics(p_truth, base_pred_p)),
        "baseline_oracle": base_oracle,  # eval-oracle best-k — upper bound, favours the baseline
        "model_threshold_sweep": model_sweep,
        "baseline_k_sweep": base_sweep,
        "pixel_iou_tp_mean": float(np.mean(ious)) if ious else None,
    }
