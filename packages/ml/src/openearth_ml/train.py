"""U-Net training CLI: cluster-grouped CV with an inner val split, physics baseline.

    uv run python -m openearth_ml.train fold --fold 0 --epochs 3   # smoke
    uv run python -m openearth_ml.train cv                          # full CV + eval → v2
    uv run python -m openearth_ml.train deployed                    # all-data model

Protocol (fix 6/7/11 / Tier 2): GroupKFold by *site-cluster* (sites < 5 km merged);
within each outer fold's train set one cluster-group is held out as inner val for
early stopping AND prob-threshold selection — the eval fold is touched once, by the
frozen model at the inner-val threshold. Positives whose own MBMP ΔR integrates to a
net-negative ΔΩ are excluded (label gate). Both the model (prob sweep) and the
baseline (k sweep) get full curves; the headline is the model at its inner-val
threshold vs the baseline at the pipeline default k = 2.

Config is TOML (``packages/ml/configs/*.toml``). Device is ``mps`` when available.
Checkpoints/logs live under ``data_dir/ml/runs/<name>/``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import torch
import typer
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader

from openearth.settings import get_settings
from openearth_ml.data import (
    ChipDataset,
    ChipRef,
    _chip_key,
    assert_no_fold_overlap,
    cluster_folds,
    compute_channel_stats,
    load_refs,
    load_tile_geo,
    site_folds,
)
from openearth_ml.eval import evaluate, model_prob, select_threshold
from openearth_ml.labelq import label_q_kg_h, quality_filter
from openearth_ml.models import DiceBCELoss, build_unet, soft_dice

app = typer.Typer(add_completion=False, help=__doc__)

_settings = get_settings()
CHIPS_DIR = _settings.data_dir / "ml" / "ch4net" / "chips"
RECOVERY_DIR = _settings.data_dir / "ml" / "ch4net" / "recovery"
RUNS_DIR = _settings.data_dir / "ml" / "runs"
EVAL_JSON = Path("scripts/data/ml_eval_v2.json")

# Stage 3 pooled global noise floor (packages/api/.../noise_floor_v1.json) — the level
# below which retrieved Q is indistinguishable from noise. Used only to report the
# share of labels below it (§9.4); a constant here to avoid an ml→api dependency.
_STAGE3_GLOBAL_FLOOR_KG_H = 24583.13

_CV_PROTOCOL = (
    "site-cluster-grouped 5-fold CV (sites < 5 km merged), inner-val early stop + "
    "prob-threshold selection, quality-filtered labels, both-sides sweeps; see ml_eval_v2.json"
)


@dataclass(frozen=True)
class TrainConfig:
    name: str = "plume_unet_v1"
    encoder: str = "resnet18"
    encoder_weights: str | None = "imagenet"
    lr: float = 3e-4
    weight_decay: float = 1e-4
    batch_size: int = 16
    max_epochs: int = 100
    patience: int = 12
    seed: int = 0
    n_splits: int = 5
    cluster_km: float = 5.0


def load_config(path: Path | None) -> TrainConfig:
    if path is None:
        return TrainConfig()
    data = tomllib.loads(path.read_text())
    return replace(TrainConfig(), **data)


def select_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _git_hash() -> str:
    """Full HEAD hash, ``-dirty`` when the tree has uncommitted changes (fix 10b)."""
    try:
        h = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        return f"{h}-dirty" if dirty else h
    except Exception:
        return "unknown"


def _manifest_sha256() -> str:
    path = CHIPS_DIR / "manifest.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "unknown"


def _seed_everything(seed: int) -> None:
    torch.manual_seed(seed)
    np.random.seed(seed)


def _val_dice(model: torch.nn.Module, loader: DataLoader, device: str) -> float:
    model.eval()
    scores: list[float] = []
    with torch.no_grad():
        for x, y in loader:
            prob = torch.sigmoid(model(x.to(device)))
            scores.append(float(soft_dice(prob, y.to(device)).item()))
    return float(np.mean(scores)) if scores else 0.0


def train_one(
    train_ds: ChipDataset,
    val_ds: ChipDataset,
    cfg: TrainConfig,
    device: str,
    out_dir: Path,
    *,
    max_epochs: int | None = None,
    early_stop: bool = True,
) -> tuple[torch.nn.Module, float, list[dict]]:
    """Train on *train_ds*. With ``early_stop`` (CV folds), checkpoint + restore the
    best inner-val Dice (patience). Without it (deployed refit), run the full budget
    and keep the last state — no best-state restore (fix 10b: comment matches behavior).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    model = build_unet(encoder_name=cfg.encoder, encoder_weights=cfg.encoder_weights).to(device)
    opt = AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    epochs = max_epochs or cfg.max_epochs
    sched = CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = DiceBCELoss()
    train_loader = DataLoader(train_ds, batch_size=cfg.batch_size, shuffle=True, drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=cfg.batch_size)

    best_dice, best_state, wait = -1.0, None, 0
    history: list[dict] = []
    for epoch in range(epochs):
        model.train()
        losses: list[float] = []
        for x, y in train_loader:
            opt.zero_grad()
            loss = loss_fn(model(x.to(device)), y.to(device))
            loss.backward()
            opt.step()
            losses.append(float(loss.item()))
        sched.step()
        val_dice = _val_dice(model, val_loader, device)
        history.append({"epoch": epoch, "train_loss": float(np.mean(losses)), "val_dice": val_dice})
        typer.echo(f"    epoch {epoch:3d}  loss {np.mean(losses):.4f}  val_dice {val_dice:.4f}")
        if val_dice > best_dice:
            best_dice, wait = val_dice, 0
            if early_stop:
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
                torch.save(best_state, out_dir / "best.pt")
        elif early_stop:
            wait += 1
            if wait >= cfg.patience:
                typer.echo(f"    early stop at epoch {epoch} (best val_dice {best_dice:.4f})")
                break
    (out_dir / "history.json").write_text(json.dumps(history))
    if early_stop and best_state is not None:
        model.load_state_dict(best_state)
    return model, best_dice, history


@app.command()
def fold(fold: int = 0, epochs: int = 3, config: Path | None = None) -> None:
    """Smoke: train one fold for a few epochs to confirm loop/device/checkpointing."""
    cfg = load_config(config)
    device = select_device()
    typer.echo(f"device = {device}")
    _seed_everything(cfg.seed)
    refs = load_refs(CHIPS_DIR)
    folds, _ = site_folds(refs, cfg.n_splits)
    tr, va = folds[fold]
    tr_refs, va_refs = [refs[i] for i in tr], [refs[i] for i in va]
    stats = compute_channel_stats(tr_refs)
    typer.echo(f"fold {fold}: {len(tr_refs)} train / {len(va_refs)} val chips")
    model, _, _ = train_one(
        ChipDataset(tr_refs, stats, augment=True, seed=cfg.seed),
        ChipDataset(va_refs, stats, augment=False),
        cfg,
        device,
        RUNS_DIR / f"{cfg.name}_smoke_fold{fold}",
        max_epochs=epochs,
    )
    res = evaluate(model, stats, va_refs, device)
    typer.echo(f"smoke eval: {json.dumps(res['model'], indent=2)}")


@app.command()
def cv(config: Path | None = None) -> None:
    """Full cluster-grouped CV with inner-val protocol → scripts/data/ml_eval_v2.json."""
    cfg = load_config(config)
    device = select_device()
    typer.echo(f"device = {device}")
    _seed_everything(cfg.seed)
    refs = load_refs(CHIPS_DIR)
    geo = load_tile_geo(RECOVERY_DIR)
    folds, fold_of_site, site_cluster = cluster_folds(refs, geo, cfg.n_splits, cfg.cluster_km)
    n_overlap = assert_no_fold_overlap(refs, geo, folds)  # aborts if > 10% cross-fold overlap
    typer.echo(
        f"clusters: {len(set(site_cluster.values()))} from {len(site_cluster)} sites; "
        f"cross-fold >10% overlap pairs = {n_overlap}"
    )
    lq = quality_filter(refs)
    kept_keys = {_chip_key(r) for r in lq.kept}
    typer.echo(f"label gate: {lq.n_excluded}/{lq.n_positive} positives excluded (ΔΩ ≤ 0)")

    fold_rows: list[dict] = []
    for f in range(cfg.n_splits):
        inner_fold = (f + 1) % cfg.n_splits
        eval_refs = [r for r in refs if fold_of_site.get(r.site_id) == f]
        inner_val = [r for r in lq.kept if fold_of_site.get(r.site_id) == inner_fold]
        inner_train = [r for r in lq.kept if fold_of_site.get(r.site_id) not in (f, inner_fold)]
        val_sites = sorted({r.site_id for r in eval_refs})
        typer.echo(
            f"=== fold {f}: eval {val_sites} ({len(eval_refs)} chips), "
            f"inner-val fold {inner_fold} ({len(inner_val)} chips) ==="
        )
        stats = compute_channel_stats(inner_train)
        model, best_dice, _ = train_one(
            ChipDataset(inner_train, stats, augment=True, seed=cfg.seed),
            ChipDataset(inner_val, stats, augment=False),
            cfg,
            device,
            RUNS_DIR / f"{cfg.name}_fold{f}",
            early_stop=True,
        )
        inner_probs = [model_prob(model, stats, r, device) for r in inner_val]
        inner_truth = [bool(np.load(r.path)["mask"].any()) for r in inner_val]
        threshold = select_threshold(inner_probs, inner_truth)
        typer.echo(f"    inner-val selected threshold = {threshold:.2f}")
        metrics = evaluate(
            model, stats, eval_refs, device, threshold=threshold, primary_keys=kept_keys
        )
        fold_rows.append(
            {
                "fold": f,
                "val_sites": val_sites,
                "inner_val_fold": inner_fold,
                "selected_threshold": threshold,
                "best_inner_val_dice": best_dice,
                **metrics,
            }
        )

    _write_eval(cfg, device, fold_of_site, site_cluster, lq, refs, fold_rows)


def _agg(fold_rows: list[dict], key: str, metric: str) -> float:
    return float(np.mean([r[key][metric] for r in fold_rows]))


def _cluster_summary(site_cluster: dict[str, int]) -> dict[str, object]:
    by_cluster: dict[int, list[str]] = {}
    for site, cid in site_cluster.items():
        by_cluster.setdefault(cid, []).append(site)
    merges = sorted(
        [sorted(ss, key=lambda s: int(s[1:])) for ss in by_cluster.values() if len(ss) > 1]
    )
    return {"n_sites": len(site_cluster), "n_clusters": len(by_cluster), "merged_groups": merges}


def _scene_sharing(refs: list[ChipRef], fold_of_site: dict[str, int]) -> dict[str, int]:
    """Residual same-acquisition target-scene sharing across folds (declared limitation)."""
    manifest = json.loads((CHIPS_DIR / "manifest.json").read_text())
    scene_folds: dict[str, set[int]] = {}
    for r in refs:
        m = manifest.get(_chip_key(r), {})
        scene = m.get("target_scene")
        f = fold_of_site.get(r.site_id)
        if scene is not None and f is not None:
            scene_folds.setdefault(scene, set()).add(f)
    shared = sum(1 for folds in scene_folds.values() if len(folds) > 1)
    return {"n_target_scenes": len(scene_folds), "n_shared_across_folds": shared}


def _below_floor(lq_kept_positives: list[ChipRef]) -> dict[str, object]:
    qs = [label_q_kg_h(r) for r in lq_kept_positives]
    below = sum(1 for q in qs if 0 < q < _STAGE3_GLOBAL_FLOOR_KG_H)
    return {
        "global_floor_kg_h": _STAGE3_GLOBAL_FLOOR_KG_H,
        "n_labels": len(qs),
        "n_below_global_floor": below,
        "share_below_global_floor": round(below / len(qs), 3) if qs else 0.0,
    }


def _write_eval(
    cfg: TrainConfig,
    device: str,
    fold_of_site: dict[str, int],
    site_cluster: dict[str, int],
    lq: object,
    refs: list[ChipRef],
    fold_rows: list[dict],
) -> None:
    kept_positives = [r for r in lq.kept if r.positive]  # type: ignore[attr-defined]
    aggregate = {
        "model_scene_f1": _agg(fold_rows, "model", "f1"),
        "model_scene_precision": _agg(fold_rows, "model", "precision"),
        "model_scene_recall": _agg(fold_rows, "model", "recall"),
        "model_scene_f1_all_labels": _agg(fold_rows, "model_all_labels", "f1"),
        "baseline_k2_f1": _agg(fold_rows, "baseline_k2", "f1"),
        "baseline_k2_precision": _agg(fold_rows, "baseline_k2", "precision"),
        "baseline_k2_recall": _agg(fold_rows, "baseline_k2", "recall"),
        "baseline_oracle_f1": _agg(fold_rows, "baseline_oracle", "f1"),
        "deployed_threshold": float(np.median([r["selected_threshold"] for r in fold_rows])),
    }
    passed = aggregate["model_scene_f1"] >= aggregate["baseline_k2_f1"]
    doc = {
        "model_version": cfg.name,
        "cv_protocol": _CV_PROTOCOL,
        "provenance": {
            "git_hash": _git_hash(),
            "data_manifest_sha256": _manifest_sha256(),
            "device": device,
            "seed": cfg.seed,
            "config": asdict(cfg),
            "fold_of_site": fold_of_site,
        },
        "clusters": _cluster_summary(site_cluster),
        "label_gate": {
            "n_positive": lq.n_positive,  # type: ignore[attr-defined]
            "n_excluded": lq.n_excluded,  # type: ignore[attr-defined]
            "frac_excluded": round(lq.n_excluded / max(1, lq.n_positive), 3),  # type: ignore[attr-defined]
        },
        "below_noise_floor": _below_floor(kept_positives),
        "scene_sharing": _scene_sharing(refs, fold_of_site),
        "aggregate": aggregate,
        "gate_model_ge_baseline": passed,
        "folds": fold_rows,
    }
    EVAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    EVAL_JSON.write_text(json.dumps(doc, indent=2))
    typer.echo(
        f"\nGATE model_scene_f1 {aggregate['model_scene_f1']:.3f} "
        f"{'>=' if passed else '<'} baseline_k2 {aggregate['baseline_k2_f1']:.3f} "
        f"(baseline oracle {aggregate['baseline_oracle_f1']:.3f}) "
        f"→ {'PASS' if passed else 'FAIL (expected — the gate is protocol validity, not metric)'}"
    )
    typer.echo(f"wrote {EVAL_JSON}")


@app.command()
def deployed(config: Path | None = None, epochs: int = 60) -> None:
    """Retrain on ALL quality-filtered chips (no early stop) → the shipped model + stats."""
    cfg = load_config(config)
    device = select_device()
    _seed_everything(cfg.seed)
    refs = quality_filter(load_refs(CHIPS_DIR)).kept  # label gate applies to the deployed model too
    stats = compute_channel_stats(refs)
    out_dir = RUNS_DIR / cfg.name
    # Fixed budget, NO early stopping, keep the last state (fix 10b).
    model, _, _ = train_one(
        ChipDataset(refs, stats, augment=True, seed=cfg.seed),
        ChipDataset(refs[: cfg.batch_size], stats, augment=False),  # tiny probe for logging
        cfg,
        device,
        out_dir,
        max_epochs=epochs,
        early_stop=False,
    )
    torch.save(model.state_dict(), out_dir / "deployed.pt")
    (out_dir / "channel_stats.json").write_text(
        json.dumps(
            {"channels": list(stats.channels), "median": list(stats.median), "mad": list(stats.mad)}
        )
    )
    typer.echo(f"deployed model + stats → {out_dir}")


if __name__ == "__main__":
    app()
