"""Pure frame post-processing for the timelapse Production pass (Phase 10 Stage 2).

The three "artifact killers" — temporal gap-fill, sequence deflicker, and a colour
grade — plus honesty measurements (valid / filled fraction) and Survey hole tint.
Everything here is pure NumPy on RGBA ``uint8`` arrays (H, W, 4), no Earth Engine:
the alpha channel IS the validity mask (EE renders masked pixels transparent), so
the layer needs no second data path and is unit-tested on tiny synthetic sequences.

Physics-honesty wall (hard rule 1): the *modifying* operations — gap-fill,
deflicker, grade, tint — refuse to run on anything but an RGB **display** frame
(``product_is_rgb``). They must never touch a scientific product's data values,
whose frame-to-frame consistency comes from the fixed vis range, not from
repainting. The *measurements* (:func:`valid_fraction`, :func:`frame_luminance`)
carry no guard — honesty surfaces are recorded for every product, whatever mode
runs (hard rule 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

# ── Declared constants (decisions 3–5) ───────────────────────────
FILL_CAP_WINDOWS = 2  # a hole inherits a valid pixel at most this many windows old
MAX_DEFLICKER_GAIN = 0.20  # ± clamp on the per-frame luminance gain
DEFLICKER_REFERENCE_WINDOW = 5  # centred rolling-median window (frames) for the anchor

GradeCurve = Literal["natural", "vivid", "cinematic"]

# Rec. 601 luma weights — the deflicker anchor and the saturation pivot.
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


class NonDisplayFrameError(ValueError):
    """Raised when a modifying post-process is asked to touch a non-RGB product.

    The honesty wall: deflicker / gap-fill / grade / tint alter *display* pixels;
    applying them to a palette-rendered scientific index would corrupt the
    data→colour mapping. Callers gate on ``spec.is_rgb``; this enforces it in code.
    """


def _require_display(product_is_rgb: bool) -> None:
    if not product_is_rgb:
        raise NonDisplayFrameError(
            "Post-processing (gap-fill / deflicker / grade / tint) is display-only "
            "and must never touch a non-RGB scientific product's data values."
        )


def _as_rgba(frame: NDArray[np.uint8]) -> NDArray[np.uint8]:
    if frame.ndim != 3 or frame.shape[2] != 4:
        raise ValueError(f"expected an (H, W, 4) RGBA frame; got shape {frame.shape}")
    return frame


# ── Honesty measurements (no guard — recorded for every product) ──


def valid_fraction(frame: NDArray[np.uint8]) -> float:
    """Fraction of pixels that carry real data (alpha > 0) — the honesty surface."""
    alpha = _as_rgba(frame)[..., 3]
    return float(np.count_nonzero(alpha) / alpha.size)


def frame_luminance(frame: NDArray[np.uint8]) -> float | None:
    """Median Rec.601 luminance over the *valid* pixels; ``None`` if none are valid."""
    frame = _as_rgba(frame)
    valid = frame[..., 3] > 0
    if not valid.any():
        return None
    rgb = frame[..., :3].astype(np.float32)
    lum = rgb @ _LUMA
    return float(np.median(lum[valid]))


# ── Gap-fill: forward-fill with a declared staleness cap (decision 3) ──


@dataclass(frozen=True)
class FrameFill:
    """Per-frame gap-fill outcome for the QC surface."""

    filled_fraction: float  # share of the frame filled from an earlier observation
    max_staleness: int  # oldest fill used, in windows (0 when nothing was filled)


class ForwardFiller:
    """Streaming forward-fill: holes inherit the most recent valid pixel ≤ cap old.

    Stateful so the frame builder can fill in window order without holding the
    whole sequence in memory; :func:`forward_fill` wraps it for whole-sequence
    tests. Staleness is measured from the last *real* observation — filled pixels
    never seed later fills, so an inherited value can never exceed the cap.
    """

    def __init__(self, *, cap_windows: int = FILL_CAP_WINDOWS, product_is_rgb: bool) -> None:
        _require_display(product_is_rgb)
        if cap_windows < 0:
            raise ValueError(f"cap_windows must be non-negative; got {cap_windows}")
        self._cap = cap_windows
        self._last_rgb: NDArray[np.uint8] | None = None
        self._age: NDArray[np.int64] | None = None

    def push(self, frame: NDArray[np.uint8]) -> tuple[NDArray[np.uint8], FrameFill]:
        """Fill *frame*'s in-cap holes; return the filled copy + its fill stats."""
        frame = _as_rgba(frame)
        h, w = frame.shape[:2]
        rgb = frame[..., :3]
        valid = frame[..., 3] > 0

        if self._last_rgb is None or self._age is None:
            self._last_rgb = np.zeros((h, w, 3), dtype=np.uint8)
            # "never observed" — larger than any reachable age so frame 0 holes stay holes.
            self._age = np.full((h, w), self._cap + 1, dtype=np.int64)

        # Age every pixel one window, then reset the ones observed this frame.
        self._age = self._age + 1
        self._last_rgb = np.where(valid[..., None], rgb, self._last_rgb)
        self._age = np.where(valid, 0, self._age)

        fillable = (~valid) & (self._age >= 1) & (self._age <= self._cap)
        out = frame.copy()
        out[..., :3] = np.where(fillable[..., None], self._last_rgb, rgb)
        out[..., 3] = np.where(fillable, np.uint8(255), frame[..., 3])

        filled_fraction = float(np.count_nonzero(fillable) / fillable.size)
        max_staleness = int(self._age[fillable].max()) if fillable.any() else 0
        return out, FrameFill(filled_fraction, max_staleness)


def forward_fill(
    frames: Sequence[NDArray[np.uint8]],
    *,
    cap_windows: int = FILL_CAP_WINDOWS,
    product_is_rgb: bool,
) -> tuple[list[NDArray[np.uint8]], list[FrameFill]]:
    """Whole-sequence forward-fill (decision 3). Returns filled frames + per-frame fills."""
    filler = ForwardFiller(cap_windows=cap_windows, product_is_rgb=product_is_rgb)
    filled: list[NDArray[np.uint8]] = []
    fills: list[FrameFill] = []
    for frame in frames:
        out, info = filler.push(frame)
        filled.append(out)
        fills.append(info)
    return filled, fills


# ── Deflicker: luminance anchor, not histogram surgery (decision 4) ──


def deflicker_gains(
    luminances: Sequence[float | None],
    *,
    strength: float,
) -> list[float]:
    """Per-frame luminance gains toward a centred rolling reference (scalars only).

    Kills exposure pumping without repainting content: for each frame the gain is
    ``rolling_reference / frame_luminance`` (the reference follows slow seasonal
    trends but not frame-to-frame jitter), blended by *strength* ∈ [0, 1] and
    clamped to ±:data:`MAX_DEFLICKER_GAIN`. Frames with no valid pixels (``None``)
    get gain 1.0. Pure scalar math — no frame touched here, so no display guard.
    """
    if not 0.0 <= strength <= 1.0:
        raise ValueError(f"strength must be in [0, 1]; got {strength}")
    n = len(luminances)
    lums = [None if lu is None else float(lu) for lu in luminances]
    half = DEFLICKER_REFERENCE_WINDOW // 2
    gains: list[float] = []
    for i in range(n):
        centre = lums[i]
        if centre is None or centre <= 0.0:
            gains.append(1.0)
            continue
        window: list[float] = []
        for j in range(max(0, i - half), min(n, i + half + 1)):
            neighbour = lums[j]
            if neighbour is not None and neighbour > 0.0:
                window.append(neighbour)
        reference = float(np.median(window)) if window else centre
        raw = reference / centre
        blended = 1.0 + strength * (raw - 1.0)
        gains.append(float(np.clip(blended, 1.0 - MAX_DEFLICKER_GAIN, 1.0 + MAX_DEFLICKER_GAIN)))
    return gains


def apply_gain(frame: NDArray[np.uint8], gain: float, *, product_is_rgb: bool) -> NDArray[np.uint8]:
    """Scale a display frame's RGB by *gain* (alpha untouched); clip to [0, 255]."""
    _require_display(product_is_rgb)
    frame = _as_rgba(frame)
    out = frame.copy()
    rgb = frame[..., :3].astype(np.float32) * gain
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def deflicker(
    frames: Sequence[NDArray[np.uint8]], *, strength: float, product_is_rgb: bool
) -> list[NDArray[np.uint8]]:
    """Whole-sequence deflicker (decision 4) — convenience wrapper + test target."""
    _require_display(product_is_rgb)
    lums = [frame_luminance(f) for f in frames]
    gains = deflicker_gains(lums, strength=strength)
    return [
        apply_gain(f, g, product_is_rgb=product_is_rgb) for f, g in zip(frames, gains, strict=True)
    ]


# ── Grade: three declared curves + three sliders (decision 5) ─────


@dataclass(frozen=True)
class GradeOptions:
    """A colour grade: a fixed tone curve plus composable slider adjustments.

    ``brightness`` / ``contrast`` ∈ [-1, 1] are neutral at 0; ``saturation`` ∈
    [0, 2] is neutral at 1 (0 = greyscale). All are display-only.
    """

    curve: GradeCurve = "natural"
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 1.0

    def is_identity(self) -> bool:
        """True when the grade would leave every pixel unchanged (skip it entirely)."""
        return (
            self.curve == "natural"
            and self.brightness == 0.0
            and self.contrast == 0.0
            and self.saturation == 1.0
        )


def _build_curves() -> dict[GradeCurve, NDArray[np.uint8]]:
    x = np.arange(256, dtype=np.float32) / 255.0
    # Natural = identity. Vivid = S-curve (more contrast/punch). Cinematic = lifted
    # blacks + softened highlights (a faded film look). All monotonic non-decreasing.
    natural = x
    vivid = np.clip(0.5 + (x - 0.5) * 1.28, 0.0, 1.0)
    cinematic = 0.055 + 0.90 * x
    return {
        "natural": np.round(natural * 255).astype(np.uint8),
        "vivid": np.round(vivid * 255).astype(np.uint8),
        "cinematic": np.round(cinematic * 255).astype(np.uint8),
    }


CURVES: dict[GradeCurve, NDArray[np.uint8]] = _build_curves()


def grade(
    frame: NDArray[np.uint8], options: GradeOptions, *, product_is_rgb: bool
) -> NDArray[np.uint8]:
    """Apply *options* to a display frame: curve → contrast → brightness → saturation."""
    _require_display(product_is_rgb)
    frame = _as_rgba(frame)
    if options.curve not in CURVES:
        raise ValueError(f"unknown grade curve {options.curve!r}")
    if not 0.0 <= options.saturation <= 2.0:
        raise ValueError(f"saturation must be in [0, 2]; got {options.saturation}")
    if not (-1.0 <= options.brightness <= 1.0 and -1.0 <= options.contrast <= 1.0):
        raise ValueError("brightness and contrast must be in [-1, 1]")

    out = frame.copy()
    graded = CURVES[options.curve][frame[..., :3]].astype(np.float64)
    graded = 127.5 + (graded - 127.5) * (1.0 + options.contrast)  # contrast around mid-grey
    graded = graded + options.brightness * 128.0  # additive brightness
    if options.saturation != 1.0:
        lum = (graded @ _LUMA.astype(np.float64))[..., None]
        graded = lum + (graded - lum) * options.saturation
    out[..., :3] = np.clip(graded, 0, 255).astype(np.uint8)
    return out


# ── Survey hole tint (honesty display for gaps that stay gaps) ────


def tint_holes(
    frame: NDArray[np.uint8], color: tuple[int, int, int], *, product_is_rgb: bool
) -> NDArray[np.uint8]:
    """Paint every remaining hole (alpha == 0) with *color* and make it opaque.

    Survey mode's honest gap display: a data hole shows as a flat flag colour
    rather than a transparent (invisible) void.
    """
    _require_display(product_is_rgb)
    frame = _as_rgba(frame)
    hole = frame[..., 3] == 0
    out = frame.copy()
    for channel in range(3):
        out[..., channel] = np.where(hole, np.uint8(color[channel]), frame[..., channel])
    out[..., 3] = np.where(hole, np.uint8(255), frame[..., 3])
    return out
