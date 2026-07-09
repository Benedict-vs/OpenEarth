"""Physics-informed input channels for the ML tier — pure NumPy, mypy strict.

This is the **train/serve consistency seam**: the same functions build the model
input at training time (``scripts/export_ch4net_chips.py``) and at scan time
(``openearth_api.services.ml``), so a chip is represented identically in both.
Nothing here touches Earth Engine or torch — it consumes already-fetched
:class:`~openearth.methane.retrieval.RetrievalChip` bands and produces plain
arrays; the API serves the model via onnxruntime without importing torch.

The five channels (order is the model's input contract — never reorder without a
new ``model_version``):

    mbmp_delta_r  target ΔR − reference ΔR   (the primary plume signal)
    mbsp_delta_r  target single-pass ΔR
    ratio_b12_b11 SWIR band ratio
    b12, b11      raw SWIR reflectance (context)

``ChannelStats`` (per-channel median/MAD) is *data*, not code: it is computed
from the training chips and frozen into the model manifest, then applied verbatim
at serve time. The API reads it from the manifest, never from constants here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from openearth.methane.plume import mask_outline_geojson
from openearth.methane.retrieval import RetrievalChip, mbmp, mbsp

if TYPE_CHECKING:
    from openearth.ee.pixels import GridSpec

# The model's input channel order. Frozen into the manifest as the serving
# contract; the manifest's channel list must equal this tuple to load.
CHANNELS: tuple[str, ...] = ("mbmp_delta_r", "mbsp_delta_r", "ratio_b12_b11", "b12", "b11")

# smp U-Net (resnet18, 5 stages) halves the grid 5×; pad serve-time chips to a
# multiple of 32 so every skip connection lines up. 16 is safe for depth-4.
PAD_MULTIPLE = 32

_MAD_TO_SIGMA = 1.4826  # robust-σ scale, matching plume.robust_sigma
_CONNECTIVITY_8 = np.ones((3, 3), dtype=bool)


@dataclass(frozen=True)
class ChannelStats:
    """Per-channel median/MAD normalisation constants (frozen in the manifest)."""

    channels: tuple[str, ...]
    median: tuple[float, ...]
    mad: tuple[float, ...]  # raw MAD; :func:`normalize` applies the 1.4826 σ factor

    def __post_init__(self) -> None:
        if tuple(self.channels) != CHANNELS:
            raise ValueError(f"ChannelStats.channels {self.channels} != CHANNELS {CHANNELS}")
        if not (len(self.median) == len(self.mad) == len(CHANNELS)):
            raise ValueError("ChannelStats median/mad must have one entry per channel")


@dataclass(frozen=True)
class PadSpec:
    """Records a :func:`pad_to_multiple` so :func:`unpad` can invert it exactly."""

    orig_h: int
    orig_w: int
    bottom: int
    right: int


@dataclass(frozen=True)
class MlCandidate:
    """One connected component of the probability map above threshold."""

    n_px: int
    mean_prob: float
    max_prob: float  # the candidate score
    mask: NDArray[np.bool_]  # (H, W) this component only
    outline: dict[str, Any] | None  # GeoJSON FeatureCollection, or None if no grid


def _band(chip: RetrievalChip, name: str) -> NDArray[np.float64]:
    return np.asarray(chip.bands[name], dtype=np.float64)


def _ratio(b12: NDArray[np.float64], b11: NDArray[np.float64]) -> NDArray[np.float64]:
    """B12/B11, NaN where either band is non-finite or B11 is zero."""
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.asarray(b12 / b11, dtype=np.float64)
    r[~np.isfinite(b11) | (b11 == 0.0) | ~np.isfinite(b12)] = np.nan
    return r


def build_channels(target: RetrievalChip, reference: RetrievalChip) -> NDArray[np.float32]:
    """Stack the five physics channels for *target* into ``(H, W, 5)`` float32.

    *target* and *reference* must share a grid (same bbox + scale ⇒ identical
    ``GridSpec``), as guaranteed by ``fetch_chip``. Masked/invalid pixels stay
    NaN here and become 0 in :func:`normalize`.
    """
    t11, t12 = _band(target, "B11"), _band(target, "B12")
    if t11.shape != _band(reference, "B11").shape:
        raise ValueError(
            f"target/reference grids differ: {t11.shape} vs {_band(reference, 'B11').shape}"
        )
    t_mbsp = mbsp(t11, t12)
    r_mbsp = mbsp(_band(reference, "B11"), _band(reference, "B12"))
    fields: dict[str, NDArray[np.float64]] = {
        "mbmp_delta_r": mbmp(t_mbsp, r_mbsp),
        "mbsp_delta_r": t_mbsp.delta_r,
        "ratio_b12_b11": _ratio(t12, t11),
        "b12": t12,
        "b11": t11,
    }
    return np.stack([fields[c] for c in CHANNELS], axis=-1).astype(np.float32)


def normalize(x: NDArray[np.float32], stats: ChannelStats) -> NDArray[np.float32]:
    """Robustly z-score each channel: ``(x − median) / (1.4826·MAD)``; NaN → 0."""
    if tuple(stats.channels) != CHANNELS:
        raise ValueError("ChannelStats channel order does not match channels.CHANNELS")
    med = np.asarray(stats.median, dtype=np.float64)
    scale = _MAD_TO_SIGMA * np.asarray(stats.mad, dtype=np.float64)
    scale = np.where(scale > 0.0, scale, 1.0)  # a flat channel ⇒ leave centred, don't divide by 0
    z = (x.astype(np.float64) - med) / scale
    return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def pad_to_multiple(x: NDArray[Any], m: int = PAD_MULTIPLE) -> tuple[NDArray[Any], PadSpec]:
    """Reflect-pad the H, W axes up to a multiple of *m* (bottom/right only)."""
    h, w = x.shape[0], x.shape[1]
    bottom, right = (-h) % m, (-w) % m
    pad_width = [(0, bottom), (0, right)] + [(0, 0)] * (x.ndim - 2)
    padded = np.pad(x, pad_width, mode="reflect") if (bottom or right) else x
    return padded, PadSpec(orig_h=h, orig_w=w, bottom=bottom, right=right)


def unpad(x: NDArray[Any], spec: PadSpec) -> NDArray[Any]:
    """Invert :func:`pad_to_multiple`, cropping back to the original H, W."""
    return x[: spec.orig_h, : spec.orig_w]


def candidates_from_prob(
    prob: NDArray[np.float32],
    *,
    threshold: float = 0.5,
    min_px: int,
    grid: GridSpec | None = None,
) -> list[MlCandidate]:
    """Connected components of ``prob ≥ threshold`` with ≥ *min_px* pixels.

    Reuses the physics tier's 8-connectivity. Candidates are returned sorted by
    score (max prob) descending. When *grid* is given, each candidate carries a
    GeoJSON outline in the same shape the physics mask uses
    (:func:`~openearth.methane.plume.mask_outline_geojson`); otherwise ``outline``
    is ``None`` and the caller georeferences the mask.
    """
    binary = np.isfinite(prob) & (prob >= threshold)
    labels, n = ndimage.label(binary, structure=_CONNECTIVITY_8)
    out: list[MlCandidate] = []
    for i in range(1, n + 1):
        comp = labels == i
        n_px = int(comp.sum())
        if n_px < min_px:
            continue
        vals = prob[comp]
        out.append(
            MlCandidate(
                n_px=n_px,
                mean_prob=float(np.mean(vals)),
                max_prob=float(np.max(vals)),
                mask=comp,
                outline=mask_outline_geojson(comp, grid) if grid is not None else None,
            )
        )
    out.sort(key=lambda c: c.max_prob, reverse=True)
    return out
