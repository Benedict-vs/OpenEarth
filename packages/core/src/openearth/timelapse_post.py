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

# ── Sequence exposure (acceptance-pass fix C: snow/highlight blowout) ──
# Windows sampled (evenly spaced, first/last always included) to estimate one
# fixed exposure for the whole render.
VIS_SAMPLE_WINDOWS = 5
# The sequence is "HDR" — worth a highlight shoulder — when its brightest sampled
# window exceeds the typical window's highlight by more than this ratio.
HIGHLIGHT_TRIGGER_RATIO = 1.25
# Small span headroom on the sampled percentiles so the p1/p99 tails aren't
# clipped exactly at the range edges.
HIGHLIGHT_HEADROOM = 0.05
# Where the shoulder knee may land in display space at most: the typical scene
# keeps up to the bottom 85 % of the tonal range linearly.
SHOULDER_KNEE_OUT = 0.85
# The shoulder's initial slope may exceed its average slope by at most this
# ratio (C¹ continuity at the knee fixes the initial slope to the linear
# section's). Beyond it the roll-off saturates almost immediately and bright
# snow would still render as flat white — the knee-out adapts down instead.
SHOULDER_MAX_SLOPE_RATIO = 3.0

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


# ── Sequence exposure: envelope range + filmic highlight shoulder (fix C) ──


def resolve_sequence_exposure(
    window_ranges: Sequence[tuple[float, float] | None],
    *,
    valid_min: float,
    valid_max: float,
) -> tuple[float, float, float | None] | None:
    """One fixed display exposure for a whole RGB sequence from sampled window stats.

    *window_ranges* holds per-sampled-window robust percentiles ``(p_lo, p_hi)``
    (``None`` for windows without stats). The exposure anchors midtones to the
    **typical** window (the 25th percentile of the window highlights, so a few
    snowy/bright windows cannot darken the whole sequence) while the minted range
    extends to the sequence's true highlight extreme — winter snow stays inside
    the range instead of clipping to flat white.

    Returns ``(lo, hi, knee_in)``: the linear vis range to mint every frame with,
    plus the normalized shoulder knee when the sequence is HDR
    (``hi_ext > HIGHLIGHT_TRIGGER_RATIO × hi_typ``), else ``None`` for a plain
    linear range. Returns ``None`` when no window had stats (caller falls back to
    the catalog range). Pure math — one result reused for every frame, so the
    exposure cannot pump.
    """
    ranges = [r for r in window_ranges if r is not None]
    if not ranges:
        return None
    los = sorted(r[0] for r in ranges)
    his = sorted(r[1] for r in ranges)
    hi_typ = float(np.percentile(his, 25))
    hi_ext = his[-1]
    span = hi_ext - los[0]
    if span <= 0:
        return None
    lo = max(los[0] - span * HIGHLIGHT_HEADROOM, valid_min)
    hi = min(hi_ext + span * HIGHLIGHT_HEADROOM, valid_max)
    if hi <= lo:
        return None
    if hi_ext <= HIGHLIGHT_TRIGGER_RATIO * hi_typ:
        return (lo, hi, None)
    knee_in = (hi_typ - lo) / (hi - lo)
    # Degenerate knees fall back to linear: a knee below 0.1 would blow up the
    # midtone slope; one at/above the knee-out has no shoulder room left.
    if not 0.1 <= knee_in < SHOULDER_KNEE_OUT:
        return (lo, hi, None)
    return (lo, hi, knee_in)


def shoulder_knee_out(knee_in: float) -> float:
    """The display position the knee maps to, adapted so highlights keep texture.

    C¹ continuity makes the shoulder's initial slope equal the linear section's
    ``knee_out/knee_in``; bounding that at :data:`SHOULDER_MAX_SLOPE_RATIO` times
    the shoulder's *average* slope ``(1−knee_out)/(1−knee_in)`` gives
    ``knee_out ≤ r·k/(1 + (r−1)·k)`` — capped at :data:`SHOULDER_KNEE_OUT`.
    Without the bound a steep midtone slope saturates the roll-off almost
    immediately and snow still clips to flat white.
    """
    r = SHOULDER_MAX_SLOPE_RATIO
    return min(SHOULDER_KNEE_OUT, r * knee_in / (1.0 + (r - 1.0) * knee_in))


def highlight_shoulder_lut(knee_in: float, knee_out: float | None = None) -> NDArray[np.uint8]:
    """A fixed filmic highlight shoulder LUT: linear to the knee, smooth roll-off above.

    Normalized minted values ``t ≤ knee_in`` map linearly to ``[0, knee_out]``
    (midtones keep their contrast); above the knee a C¹-continuous shoulder
    ``knee_out + (1 − knee_out)·(1 − (1 − u)^p)`` compresses the highlights into
    the remaining headroom, reaching 1.0 exactly at ``t = 1``. ``p`` is chosen so
    the slope is continuous at the knee (``p > 1`` whenever ``knee_in < knee_out``,
    so the curve is monotone); *knee_out* defaults to :func:`shoulder_knee_out`,
    which bounds ``p`` so the highlights keep real gradation. The LUT is fixed
    for a whole render.
    """
    if knee_out is None:
        knee_out = shoulder_knee_out(knee_in)
    if not 0.0 < knee_in < knee_out < 1.0:
        raise ValueError(f"need 0 < knee_in < knee_out < 1; got {knee_in}, {knee_out}")
    t = np.linspace(0.0, 1.0, 256)
    y = np.empty_like(t)
    below = t <= knee_in
    y[below] = t[below] * (knee_out / knee_in)
    u = (t[~below] - knee_in) / (1.0 - knee_in)
    p = knee_out * (1.0 - knee_in) / (knee_in * (1.0 - knee_out))
    y[~below] = knee_out + (1.0 - knee_out) * (1.0 - (1.0 - u) ** p)
    return np.round(np.clip(y, 0.0, 1.0) * 255.0).astype(np.uint8)


def apply_lut(
    frame: NDArray[np.uint8], lut: NDArray[np.uint8], *, product_is_rgb: bool
) -> NDArray[np.uint8]:
    """Apply a fixed 256-entry tone LUT to a display frame's RGB (alpha untouched)."""
    _require_display(product_is_rgb)
    frame = _as_rgba(frame)
    if lut.shape != (256,):
        raise ValueError(f"expected a 256-entry LUT; got shape {lut.shape}")
    out = frame.copy()
    out[..., :3] = lut[frame[..., :3]]
    return out


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
