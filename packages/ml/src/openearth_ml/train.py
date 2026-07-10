"""U-Net training CLI: site-held-out CV, physics-baseline eval, deployed refit.

    uv run python -m openearth_ml.train fold --fold 0 --epochs 3   # smoke
    uv run python -m openearth_ml.train cv                          # full CV + eval
    uv run python -m openearth_ml.train deployed                    # all-data model

Config is TOML (``packages/ml/configs/*.toml``); defaults match the plan (Dice+BCE,
AdamW 3e-4 / wd 1e-4, cosine, batch 16, ≤100 epochs, early stop on fold-val Dice,
seed 0). Device is ``mps`` when available else ``cpu`` — logged and recorded in the
eval provenance (MPS may be unavailable on this macOS; CPU fp32 is fine at this
dataset size). Checkpoints/logs live under ``data_dir/ml/runs/<name>/``.
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
    compute_channel_stats,
    load_refs,
    site_folds,
)
from openearth_ml.eval import evaluate
from openearth_ml.models import DiceBCELoss, build_unet, soft_dice

app = typer.Typer(add_completion=False, help=__doc__)

_settings = get_settings()
CHIPS_DIR = _settings.data_dir / "ml" / "ch4net" / "chips"
RUNS_DIR = _settings.data_dir / "ml" / "runs"
EVAL_JSON = Path("scripts/data/ml_eval_v1.json")


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
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
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
) -> tuple[torch.nn.Module, float, list[dict]]:
    """Train with early stopping on val Dice; returns (best-state model, dice, log)."""
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
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            torch.save(best_state, out_dir / "best.pt")
        else:
            wait += 1
            if wait >= cfg.patience:
                typer.echo(f"    early stop at epoch {epoch} (best val_dice {best_dice:.4f})")
                break
    (out_dir / "history.json").write_text(json.dumps(history))
    if best_state is not None:
        model.load_state_dict(best_state)
    return model, best_dice, history


@app.command()
def fold(
    fold: int = 0,
    epochs: int = 3,
    config: Path | None = None,
) -> None:
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
    typer.echo(f"smoke eval: {json.dumps(res, indent=2)}")


@app.command()
def cv(config: Path | None = None) -> None:
    """Full site-held-out CV + physics-baseline eval → scripts/data/ml_eval_v1.json."""
    cfg = load_config(config)
    device = select_device()
    typer.echo(f"device = {device}")
    _seed_everything(cfg.seed)
    refs = load_refs(CHIPS_DIR)
    folds, fold_of = site_folds(refs, cfg.n_splits)

    fold_rows: list[dict] = []
    for f, (tr, va) in enumerate(folds):
        tr_refs, va_refs = [refs[i] for i in tr], [refs[i] for i in va]
        val_sites = sorted({r.site_id for r in va_refs})
        typer.echo(f"=== fold {f}: hold out {val_sites} ({len(va_refs)} chips) ===")
        stats = compute_channel_stats(tr_refs)
        model, best_dice, _ = train_one(
            ChipDataset(tr_refs, stats, augment=True, seed=cfg.seed),
            ChipDataset(va_refs, stats, augment=False),
            cfg,
            device,
            RUNS_DIR / f"{cfg.name}_fold{f}",
        )
        metrics = evaluate(model, stats, va_refs, device)
        fold_rows.append({"fold": f, "val_sites": val_sites, "best_val_dice": best_dice, **metrics})

    _write_eval(cfg, device, fold_of, fold_rows)


def _agg(fold_rows: list[dict], key: str, metric: str) -> float:
    return float(np.mean([r[key][metric] for r in fold_rows]))


def _write_eval(
    cfg: TrainConfig, device: str, fold_of: dict[str, int], fold_rows: list[dict]
) -> None:
    aggregate: dict[str, float] = {
        "model_scene_f1": _agg(fold_rows, "model", "f1"),
        "model_scene_precision": _agg(fold_rows, "model", "precision"),
        "model_scene_recall": _agg(fold_rows, "model", "recall"),
        "baseline_scene_f1": _agg(fold_rows, "baseline", "f1"),
        "baseline_scene_precision": _agg(fold_rows, "baseline", "precision"),
        "baseline_scene_recall": _agg(fold_rows, "baseline", "recall"),
    }
    passed = aggregate["model_scene_f1"] >= aggregate["baseline_scene_f1"]
    doc = {
        "model_version": cfg.name,
        "provenance": {
            "git_hash": _git_hash(),
            "data_manifest_sha256": _manifest_sha256(),
            "device": device,
            "seed": cfg.seed,
            "config": asdict(cfg),
            "fold_of_site": fold_of,
        },
        "aggregate": aggregate,
        "gate_model_ge_baseline": passed,
        "folds": fold_rows,
    }
    EVAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    EVAL_JSON.write_text(json.dumps(doc, indent=2))
    typer.echo(
        f"\nGATE model_scene_f1 {aggregate['model_scene_f1']:.3f} "
        f"{'>=' if passed else '<'} baseline {aggregate['baseline_scene_f1']:.3f} "
        f"→ {'PASS' if passed else 'FAIL — diagnose before Stage 3'}"
    )
    typer.echo(f"wrote {EVAL_JSON}")


@app.command()
def deployed(config: Path | None = None, epochs: int = 60) -> None:
    """Retrain on ALL chips (full-trainset stats) → the shipped model + its stats."""
    cfg = load_config(config)
    device = select_device()
    _seed_everything(cfg.seed)
    refs = load_refs(CHIPS_DIR)
    stats = compute_channel_stats(refs)
    out_dir = RUNS_DIR / cfg.name
    # no held-out val: train a fixed budget, checkpoint the last state
    model, _, _ = train_one(
        ChipDataset(refs, stats, augment=True, seed=cfg.seed),
        ChipDataset(refs[: cfg.batch_size], stats, augment=False),  # tiny probe for logging
        cfg,
        device,
        out_dir,
        max_epochs=epochs,
    )
    torch.save(model.state_dict(), out_dir / "deployed.pt")
    (out_dir / "channel_stats.json").write_text(
        json.dumps(
            {
                "channels": list(stats.channels),
                "median": list(stats.median),
                "mad": list(stats.mad),
            }
        )
    )
    typer.echo(f"deployed model + stats → {out_dir}")


if __name__ == "__main__":
    app()
