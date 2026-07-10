"""Model forward pass, loss, and eval logic (tiny tensors, no pretrained download)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from openearth.methane.channels import CHANNELS, ChannelStats
from openearth_ml.data import load_refs
from openearth_ml.eval import baseline_scene_positive, evaluate, scene_metrics
from openearth_ml.models import DiceBCELoss, build_unet, soft_dice


def test_unet_forward_shape() -> None:
    model = build_unet(in_channels=5, encoder_weights=None)  # None ⇒ no network fetch
    x = torch.zeros(2, 5, 64, 64)
    out = model(x)
    assert tuple(out.shape) == (2, 1, 64, 64)


def test_dice_bce_loss_and_soft_dice() -> None:
    logits = torch.full((1, 1, 8, 8), 10.0)  # ~all-ones prediction
    target = torch.ones(1, 1, 8, 8)
    assert soft_dice(torch.sigmoid(logits), target).item() > 0.99
    loss = DiceBCELoss()(logits, target)
    assert loss.item() < 0.05  # confident-correct ⇒ near-zero


def test_scene_metrics_f1() -> None:
    truth = [True, True, False, False]
    pred = [True, False, False, True]  # 1 tp, 1 fn, 1 fp, 1 tn
    m = scene_metrics(truth, pred)
    assert (m.tp, m.fp, m.fn, m.tn) == (1, 1, 1, 1)
    assert m.precision == 0.5
    assert m.recall == 0.5
    assert m.f1 == 0.5


def test_baseline_scene_positive_fires_on_strong_mbmp() -> None:
    channels = np.zeros((30, 30, 5), dtype=np.float32)
    channels += np.random.default_rng(0).normal(0, 0.1, channels.shape).astype(np.float32)
    channels[10:16, 10:16, 0] = -5.0  # strong −ΔR_MBMP enhancement
    assert baseline_scene_positive(channels) is True
    assert baseline_scene_positive(np.zeros((30, 30, 5), dtype=np.float32)) is False


def test_evaluate_end_to_end_with_untrained_model(chips_dir: Path) -> None:
    refs = load_refs(chips_dir)
    model = build_unet(in_channels=5, encoder_weights=None)
    stats = ChannelStats(CHANNELS, (0.0,) * 5, (1.0,) * 5)
    res = evaluate(model, stats, refs, "cpu")
    assert res["n"] == len(refs)
    assert set(res["model"]) >= {"f1", "precision", "recall", "tp", "fp", "fn"}
    # the physics baseline should fire on the synthetic positives (strong −ΔR_MBMP)
    assert res["baseline"]["recall"] == 1.0
