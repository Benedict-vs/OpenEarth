"""U-Net model + segmentation loss.

``segmentation_models_pytorch`` gives a resnet18 U-Net. With ``in_channels=5`` the
first conv is randomly initialised even with ``encoder_weights='imagenet'`` (the
rest of the encoder is still pretrained) — the ``encoder_weights=None`` ablation
in eval measures how much that partial pretraining actually buys.
"""

from __future__ import annotations

import segmentation_models_pytorch as smp
import torch
from torch import nn


def build_unet(
    *,
    in_channels: int = 5,
    encoder_name: str = "resnet18",
    encoder_weights: str | None = "imagenet",
) -> nn.Module:
    """A resnet18 U-Net emitting a single-channel logit map."""
    model: nn.Module = smp.Unet(
        encoder_name=encoder_name,
        encoder_weights=encoder_weights,
        in_channels=in_channels,
        classes=1,
    )
    return model


def soft_dice(prob: torch.Tensor, target: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    """Soft Dice coefficient over a batch of (B, 1, H, W) probabilities."""
    dims = (1, 2, 3)
    inter = (prob * target).sum(dims)
    denom = prob.sum(dims) + target.sum(dims)
    return ((2.0 * inter + eps) / (denom + eps)).mean()


class DiceBCELoss(nn.Module):
    """Equal-weight Dice + BCE on logits (the pinned default)."""

    def __init__(self, dice_weight: float = 0.5, bce_weight: float = 0.5):
        super().__init__()
        self.dice_weight = dice_weight
        self.bce_weight = bce_weight
        self.bce = nn.BCEWithLogitsLoss()

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        bce = self.bce(logits, target)
        dice = 1.0 - soft_dice(torch.sigmoid(logits), target)
        return self.bce_weight * bce + self.dice_weight * dice
