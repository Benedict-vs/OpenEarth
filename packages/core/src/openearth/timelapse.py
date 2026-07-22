"""Timelapse: frame stepping over a date range + burned-in Pillow annotations.

This module is split like the rest of the library. The **pure layer** here
(stage 1) — :func:`frame_windows` and the Pillow annotation helpers — does
frame arithmetic and image compositing on plain data, so it is unit-tested
offline with no Earth Engine round-trips. The **EE + encoding layer** (stage 2,
:func:`render_frames`/:func:`encode_movie`) sits on top and is the only part
that mints thumbnails and writes movies.

Pillow is an imaging library, not a UI framework; ``test_no_ui_deps`` allows
it. We deliberately use ``ImageFont.load_default(size=…)`` (needs Pillow ≥ 10.1)
so no TTF is committed and the repo stays font-license-free.
"""

from __future__ import annotations

import io
import json
import math
import os
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from openearth.catalog import get_dataset
from openearth.composites import CompositeMode, build_composite
from openearth.ee.render import compute_vis_range, geo_dimensions, rgb_range_stats, thumb_url
from openearth.errors import EmptyCollectionError, JobError, classify_ee_error
from openearth.geometry import BBox
from openearth.timelapse_post import (
    FILL_CAP_WINDOWS,
    VIS_SAMPLE_WINDOWS,
    ForwardFiller,
    GradeOptions,
    NonDisplayFrameError,
    apply_gain,
    apply_lut,
    blend_fill_seams,
    deflicker_gains,
    frame_luminance,
    grade,
    highlight_shoulder_lut,
    resolve_sequence_exposure,
    shoulder_knee_out,
    tint_holes,
    valid_fraction,
)

if TYPE_CHECKING:
    from openearth.catalog.models import ProductSpec
    from openearth.geometry import ROI

# One degree of latitude in metres (WGS84 mean) — the same convention the pixel
# grid math (ee/pixels.py) and the web-side geo box use.
_M_PER_DEG = 111_320.0

# ── Frame-budget constants ───────────────────────────────────────
MAX_FRAMES = 400  # request 422s above this before any EE work
MAX_DIM_VIDEO = 1920  # longest edge, mp4/webm
MAX_DIM_GIF = 720  # Pillow holds every GIF frame in RAM
FRAME_FETCH_WORKERS = 4  # ThreadPoolExecutor width (EE semaphore still gates round-trips)
# Dead-pipeline breaker: if the first this-many *completed* windows yield no
# rendered frame yet at least one failed, Earth Engine is failing consistently —
# abort instead of burning the remaining mints (empty windows alone never trip
# it; a run of winter gaps is legitimate).
EARLY_ABORT_PROBE = 8

StepMode = Literal["interval", "monthly", "quarterly"]


@dataclass(frozen=True)
class FrameWindow:
    """One frame's date window and its burned-in label.

    ``start`` and ``end`` are both **inclusive** (the composite for the frame
    is built over ``[start, end]``); ``label`` is the text drawn onto the frame.
    """

    index: int
    start: date
    end: date
    label: str


def _interval_label(start: date, end: date) -> str:
    """Hyphenated range for multi-day windows; the single date when one day.

    ASCII hyphen only: the label is burned in with Pillow's bundled default
    font, which has no en-dash glyph (it rendered as a tofu box).
    """
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} - {end.isoformat()}"


def _first_of_next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def frame_windows(
    start: date,
    end: date,
    *,
    mode: StepMode = "interval",
    interval_days: int = 16,
    window_days: int | None = None,
) -> list[FrameWindow]:
    """Step ``[start, end]`` into inclusive frame windows.

    - ``interval``: a window opens every ``interval_days`` from ``start`` and
      spans ``window_days or interval_days`` days (so ``window_days >
      interval_days`` gives rolling overlap); the last window clips to ``end``.
    - ``monthly`` / ``quarterly``: calendar months / quarters intersecting
      ``[start, end]``, clipped at both ends.

    Raises :class:`ValueError` on ``end < start``, non-positive intervals, or a
    frame count over :data:`MAX_FRAMES`.
    """
    if end < start:
        raise ValueError(f"end ({end}) must not be before start ({start}).")

    if mode == "interval":
        windows = _interval_windows(start, end, interval_days, window_days)
    elif mode == "monthly":
        windows = _monthly_windows(start, end)
    elif mode == "quarterly":
        windows = _quarterly_windows(start, end)
    else:  # pragma: no cover - Literal guards this at the type level
        raise ValueError(f"Unknown step mode {mode!r}.")

    if len(windows) > MAX_FRAMES:
        raise ValueError(
            f"{len(windows)} frames exceeds the {MAX_FRAMES}-frame limit; "
            "use a coarser step or a shorter date range."
        )
    return windows


def _interval_windows(
    start: date, end: date, interval_days: int, window_days: int | None
) -> list[FrameWindow]:
    if interval_days <= 0:
        raise ValueError(f"interval_days must be positive; got {interval_days}.")
    span = window_days if window_days is not None else interval_days
    if span <= 0:
        raise ValueError(f"window_days must be positive; got {window_days}.")

    # Count up front so a tiny interval over a long range can't build a huge
    # list before the MAX_FRAMES guard trips.
    n = (end - start).days // interval_days + 1
    if n > MAX_FRAMES:
        raise ValueError(
            f"{n} frames exceeds the {MAX_FRAMES}-frame limit; "
            "use a larger interval or a shorter date range."
        )

    windows: list[FrameWindow] = []
    for i in range(n):
        ws = start + timedelta(days=i * interval_days)
        we = min(ws + timedelta(days=span - 1), end)
        windows.append(FrameWindow(i, ws, we, _interval_label(ws, we)))
    return windows


def _monthly_windows(start: date, end: date) -> list[FrameWindow]:
    windows: list[FrameWindow] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        month_end = _first_of_next_month(cursor) - timedelta(days=1)
        ws = max(cursor, start)
        we = min(month_end, end)
        label = f"{cursor.year:04d}-{cursor.month:02d}"
        windows.append(FrameWindow(len(windows), ws, we, label))
        cursor = _first_of_next_month(cursor)
    return windows


def _quarterly_windows(start: date, end: date) -> list[FrameWindow]:
    windows: list[FrameWindow] = []
    q_start_month = ((start.month - 1) // 3) * 3 + 1
    cursor = date(start.year, q_start_month, 1)
    while cursor <= end:
        quarter = (cursor.month - 1) // 3 + 1
        if quarter == 4:
            next_q = date(cursor.year + 1, 1, 1)
        else:
            next_q = date(cursor.year, cursor.month + 3, 1)
        q_end = next_q - timedelta(days=1)
        ws = max(cursor, start)
        we = min(q_end, end)
        label = f"{cursor.year:04d}-Q{quarter}"
        windows.append(FrameWindow(len(windows), ws, we, label))
        cursor = next_q
    return windows


# ── Annotation helpers (pure Pillow) ─────────────────────────────

# Round "nice" scale-bar lengths in km: 1/2/5 × 10ⁿ from 10 m to 10 000 km.
_NICE_KM = [factor * 10.0**exp for exp in range(-2, 5) for factor in (1.0, 2.0, 5.0)]

# Fraction of the frame width the scale bar may span at most.
_SCALE_BAR_MAX_FRACTION = 0.25


def scale_bar_spec(bbox: BBox, width_px: int) -> tuple[float, int]:
    """Pick a round scale-bar length for a frame *width_px* wide over *bbox*.

    Returns ``(km, px)``: the largest 1/2/5×10ⁿ km length whose on-screen span
    is ≤ ~25 % of the frame width, and that span in pixels. Longitude is
    cosine-corrected at the box centre latitude (same ``_M_PER_DEG`` as the
    pixel grid), so the bar is metrically honest.
    """
    center_lat, _ = bbox.center
    total_width_m = bbox.width_deg * _M_PER_DEG * math.cos(math.radians(center_lat))
    if total_width_m <= 0 or width_px <= 0:
        return (_NICE_KM[0], 1)

    m_per_px = total_width_m / width_px
    max_bar_m = total_width_m * _SCALE_BAR_MAX_FRACTION

    # Largest nice length that fits; fall back to the smallest if none do.
    km = _NICE_KM[0]
    for candidate in _NICE_KM:
        if candidate * 1000.0 <= max_bar_m:
            km = candidate
        else:
            break

    px = max(1, round(km * 1000.0 / m_per_px))
    return (km, px)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _sample_palette(palette: list[str], t: float) -> tuple[int, int, int]:
    """Piecewise-linear colour at fraction ``t`` in [0, 1] across *palette*."""
    if not palette:
        return (255, 255, 255)
    if len(palette) == 1:
        return _hex_to_rgb(palette[0])
    pos = max(0.0, min(1.0, t)) * (len(palette) - 1)
    i = int(pos)
    if i >= len(palette) - 1:
        return _hex_to_rgb(palette[-1])
    frac = pos - i
    c0 = _hex_to_rgb(palette[i])
    c1 = _hex_to_rgb(palette[i + 1])
    return (
        round(c0[0] + (c1[0] - c0[0]) * frac),
        round(c0[1] + (c1[1] - c0[1]) * frac),
        round(c0[2] + (c1[2] - c0[2]) * frac),
    )


def _fmt_value(value: float) -> str:
    """Compact numeric label — trims trailing zeros, avoids sci-notation noise."""
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1000 or magnitude < 0.01:
        return f"{value:.2e}"
    text = f"{value:.3g}"
    return text


def _fmt_km(km: float) -> str:
    if km >= 1:
        return f"{km:g} km"
    return f"{round(km * 1000)} m"


def render_colorbar(
    palette: list[str],
    vis_min: float,
    vis_max: float,
    *,
    width: int,
    height: int,
) -> Image.Image:
    """Horizontal gradient strip (RGBA) with min/max tick labels beneath it."""
    img = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_size = max(9, round(height * 0.42))
    font = ImageFont.load_default(size=font_size)
    label_h = font_size + 4
    bar_h = max(1, height - label_h)

    for x in range(img.width):
        t = x / (img.width - 1) if img.width > 1 else 0.0
        r, g, b = _sample_palette(palette, t)
        draw.line([(x, 0), (x, bar_h - 1)], fill=(r, g, b, 255))

    lo, hi = _fmt_value(vis_min), _fmt_value(vis_max)
    draw.text((0, bar_h + 2), lo, fill=(255, 255, 255, 255), font=font)
    hi_w = draw.textlength(hi, font=font)
    draw.text((img.width - hi_w, bar_h + 2), hi, fill=(255, 255, 255, 255), font=font)
    return img


def annotate_frame(
    img: Image.Image,
    *,
    label: str,
    attribution: str,
    colorbar: Image.Image | None,
    scale_bar: tuple[float, int] | None,
) -> Image.Image:
    """Composite a translucent bottom strip onto *img* with frame metadata.

    Layout along the strip: the date ``label`` at the left, the ``scale_bar``
    centred, and the ``colorbar`` + ``attribution`` at the right. Returns a new
    RGBA image the same size as *img*.
    """
    base = img.convert("RGBA")
    w, h = base.size

    font_size = max(11, round(h * 0.024))
    font = ImageFont.load_default(size=font_size)
    small_size = max(9, round(font_size * 0.72))
    small_font = ImageFont.load_default(size=small_size)
    pad = max(6, round(font_size * 0.5))

    strip_h = font_size + 2 * pad
    if colorbar is not None:
        strip_h = max(strip_h, colorbar.height + small_size + 3 * pad)
    strip_top = h - strip_h

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle([0, strip_top, w, h], fill=(0, 0, 0, 140))

    label_y = strip_top + (strip_h - font_size) // 2
    if label:
        odraw.text((pad, label_y), label, fill=(255, 255, 255, 255), font=font)

    if scale_bar is not None:
        _draw_scale_bar(odraw, scale_bar, frame_w=w, frame_h=h, pad=pad, font=font)

    right_edge = w - pad
    if colorbar is not None:
        cb_x = w - pad - colorbar.width
        cb_y = strip_top + pad
        overlay.alpha_composite(colorbar, (cb_x, cb_y))
        right_edge = w - pad  # attribution sits under the colorbar
        if attribution:
            aw = odraw.textlength(attribution, font=small_font)
            odraw.text(
                (right_edge - aw, cb_y + colorbar.height + 2),
                attribution,
                fill=(220, 220, 220, 255),
                font=small_font,
            )
    elif attribution:
        aw = odraw.textlength(attribution, font=small_font)
        odraw.text(
            (right_edge - aw, strip_top + (strip_h - small_size) // 2),
            attribution,
            fill=(220, 220, 220, 255),
            font=small_font,
        )

    return Image.alpha_composite(base, overlay)


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    scale_bar: tuple[float, int],
    *,
    frame_w: int,
    frame_h: int,
    pad: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    km, px = scale_bar
    tick_h = max(4, pad)
    bar_y = frame_h - pad - tick_h
    cx = frame_w // 2
    x0 = cx - px // 2
    x1 = x0 + px

    white = (255, 255, 255, 255)
    draw.line([(x0, bar_y), (x1, bar_y)], fill=white, width=2)
    draw.line([(x0, bar_y - tick_h), (x0, bar_y)], fill=white, width=2)
    draw.line([(x1, bar_y - tick_h), (x1, bar_y)], fill=white, width=2)

    text = _fmt_km(km)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw = right - left
    th = bottom - top
    draw.text((cx - tw / 2, bar_y - tick_h - th - 2), text, fill=white, font=font)


# ── EE + encoding layer ──────────────────────────────────────────

# Frames are fetched with urllib (export.py precedent — core keeps no HTTP
# client dependency); the fetch is injectable so offline tests supply bytes.
FetchFn = Callable[[str], bytes]

FrameStatus = Literal["rendered", "empty", "failed"]
MovieFormat = Literal["mp4", "gif", "webm"]

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class AnnotationOptions:
    """Which burn-ins to composite onto each frame.

    ``attribution`` overrides the dataset's own attribution string when set.
    """

    date_label: bool = True
    colorbar: bool = True
    scale_bar: bool = True
    attribution: str | None = None


@dataclass(frozen=True)
class PostOptions:
    """Display-frame post-processing knobs (Phase 10 — all default to off/legacy).

    ``gap_fill`` forward-fills holes ≤ :data:`FILL_CAP_WINDOWS` old; ``deflicker_strength``
    ∈ [0, 1] (0 = off) matches per-frame luminance to a rolling anchor; ``grade`` is a
    colour grade; ``tint_hole_color`` paints any remaining holes for Survey honesty.
    Every one touches RGB *display* frames only — the honesty wall (hard rule 1).
    """

    gap_fill: bool = False
    deflicker_strength: float = 0.0
    grade: GradeOptions | None = None
    tint_hole_color: tuple[int, int, int] | None = None

    def modifies_pixels(self) -> bool:
        """True when any knob would alter or annotate pixels before the burn-in."""
        return (
            self.gap_fill
            or self.deflicker_strength > 0.0
            or (self.grade is not None and not self.grade.is_identity())
            or self.tint_hole_color is not None
        )

    def to_manifest(self) -> dict[str, Any]:
        grade = self.grade
        return {
            "gap_fill": self.gap_fill,
            "gap_fill_cap_windows": FILL_CAP_WINDOWS if self.gap_fill else None,
            # Fix D rides with gap-fill: borrowed regions are exposure-matched and
            # feathered (in-mask only — provenance untouched).
            "seam_blend": self.gap_fill,
            "deflicker_strength": self.deflicker_strength,
            "grade": None
            if grade is None
            else {
                "curve": grade.curve,
                "brightness": grade.brightness,
                "contrast": grade.contrast,
                "saturation": grade.saturation,
            },
            "tint_hole_color": None
            if self.tint_hole_color is None
            else "#{:02x}{:02x}{:02x}".format(*self.tint_hole_color),
        }


@dataclass(frozen=True)
class FrameResult:
    """The outcome for one window: a rendered PNG on disk, empty, or failed.

    ``source`` is the dataset the frame actually came from (the primary, or a
    fallback when the source ladder stepped down); ``valid_fraction`` /
    ``filled_fraction`` are the per-frame honesty surfaces (hard rule 3).
    """

    window: FrameWindow
    status: FrameStatus
    path: Path | None
    source: str | None = None
    valid_fraction: float | None = None
    filled_fraction: float | None = None


@dataclass(frozen=True)
class FrameManifest:
    """Everything a movie encoder and the gallery need about a render."""

    dataset: str
    product: str
    width: int
    height: int
    vis: tuple[float, float]
    results: list[FrameResult] = field(default_factory=list)
    # True when the render was stopped mid-way but had ≥1 rendered frame to keep
    # (a "partial" render); False for a normal complete render.
    cancelled: bool = False
    # Phase 10 manifest v2 (additive — v1 readers ignore these): the compositing
    # reducer and the post-processing settings that produced this render.
    composite: CompositeMode = "mean"
    post: dict[str, Any] = field(default_factory=dict)
    # The region's native sensor limit (longest edge, px) at render time. Renders
    # may exceed it since the decision-9 reversal (upscaling allowed); recording
    # it here keeps the "render 1080 px · native 445 px" honesty readout possible.
    native_max_dim: int | None = None
    # The fixed highlight-shoulder tone curve applied to every frame of an HDR
    # RGB sequence (fix C), e.g. {"knee_in": 0.34, "knee_out": 0.85}; None when
    # the render is plain linear. Display-only; ``vis`` is the true minted range.
    tone: dict[str, Any] | None = None

    @property
    def frame_paths(self) -> list[Path]:
        """Dense, in-order paths of the rendered frames (no holes)."""
        return [r.path for r in self.results if r.status == "rendered" and r.path is not None]

    @property
    def rendered_count(self) -> int:
        return sum(1 for r in self.results if r.status == "rendered")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable manifest: movie (dense) index → date window.

        Rendered frames carry their dense movie index (aligned with the
        ``frame_XXXX.png`` filenames); skipped windows record ``index=null``
        with their empty/failed status so the gallery is honest.
        """
        frames: list[dict[str, Any]] = []
        dense = 0
        for r in self.results:
            index: int | None = None
            if r.status == "rendered":
                index = dense
                dense += 1
            frames.append(
                {
                    "index": index,
                    "start": r.window.start.isoformat(),
                    "end": r.window.end.isoformat(),
                    "label": r.window.label,
                    "status": r.status,
                    # Honesty surfaces (hard rule 3) — present for every frame.
                    "source": r.source,
                    "valid_fraction": r.valid_fraction,
                    "filled_fraction": r.filled_fraction,
                }
            )
        return {
            "dataset": self.dataset,
            "product": self.product,
            "width": self.width,
            "height": self.height,
            "vis": [self.vis[0], self.vis[1]],
            "cancelled": self.cancelled,
            "composite": self.composite,
            "post": self.post,
            "native_max_dim": self.native_max_dim,
            "tone": self.tone,
            "frames": frames,
        }


def _fetch_bytes(url: str) -> bytes:
    """Default frame fetcher: GET an Earth-Engine-minted thumb URL.

    No retry (the URL mint already retried through ``ee_call``); a non-200
    raises ``urllib.error.HTTPError``, which the caller records as ``failed``.
    """
    with urllib.request.urlopen(url) as response:  # EE-minted URL
        data: bytes = response.read()
    return data


def _is_empty_error(exc: BaseException) -> bool:
    """True when *exc* means the composite had no imagery (skip, don't fail)."""
    if isinstance(exc, EmptyCollectionError):
        return True
    return classify_ee_error(exc)[0] == "empty"


def _frame_dimensions(bbox: BBox, max_dim: int, even_dims: bool) -> tuple[int, int]:
    """Aspect-correct (W, H) for every frame; rounded down to even for video.

    yuv420p/libx264 reject odd dimensions and every frame must match the movie
    exactly, so the rounding happens once, up front, before any frame renders.
    """
    dims = geo_dimensions(bbox, max_dim)
    if "x" in dims:
        w_str, h_str = dims.split("x")
        w, h = int(w_str), int(h_str)
    else:  # geo_dimensions falls back to a bare longest-edge for degenerate aspect
        w = h = int(dims)
    if even_dims:
        w -= w % 2
        h -= h % 2
    return (max(2, w), max(2, h))


def _sample_rgb_exposure(
    dataset: str,
    product: str,
    spec: ProductSpec,
    roi: ROI,
    windows: list[FrameWindow],
    composite_mode: CompositeMode,
) -> tuple[float, float, float | None] | None:
    """Sample RGB percentiles on evenly spaced windows → one sequence exposure.

    Up to :data:`VIS_SAMPLE_WINDOWS` windows (first/last always included) each
    contribute robust ``(p1, p99)`` stats; a window that is empty or errors just
    contributes nothing (resilience — a broken probe must not kill the render).
    ``None`` when no window had stats.
    """
    n = len(windows)
    k = min(VIS_SAMPLE_WINDOWS, n)
    indices = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)}) if k > 1 else [0]
    ranges: list[tuple[float, float] | None] = []
    for idx in indices:
        window = windows[idx]
        try:
            image = build_composite(
                product, roi, window.start, window.end, source=dataset, mode=composite_mode
            )
            ranges.append(rgb_range_stats(image, spec, roi))
        except Exception:  # a failed probe window is "no stats", never fatal
            ranges.append(None)
    return resolve_sequence_exposure(ranges, valid_min=spec.valid_min, valid_max=spec.valid_max)


def _resolve_vis_range(
    dataset: str,
    product: str,
    spec: ProductSpec,
    roi: ROI,
    windows: list[FrameWindow],
    vis_min: float | None,
    vis_max: float | None,
    composite_mode: CompositeMode,
) -> tuple[float, float, float | None]:
    """One vis range for the whole render (no per-frame auto-scale flicker).

    Returns ``(lo, hi, knee_in)``. Request overrides win verbatim. Fully-auto
    RGB gets the sampled sequence exposure (fix C): an envelope range over
    sampled windows, plus a highlight-shoulder knee when the sequence is HDR
    (snow scenes) — ``knee_in`` is ``None`` for a plain linear range. Non-RGB
    keeps the ``compute_vis_range`` middle-window path. Computed once and
    reused for every frame and the colorbar.
    """
    if vis_min is not None and vis_max is not None:
        return (vis_min, vis_max, None)

    if spec.is_rgb:
        if vis_min is None and vis_max is None:
            sampled = _sample_rgb_exposure(dataset, product, spec, roi, windows, composite_mode)
            if sampled is not None:
                return sampled
        # Partial override / no stats: catalog defaults fill the missing side.
        lo = vis_min if vis_min is not None else spec.vis_min
        hi = vis_max if vis_max is not None else spec.vis_max
        return (lo, hi, None)

    mid = windows[len(windows) // 2]
    mid_image = build_composite(
        product, roi, mid.start, mid.end, source=dataset, mode=composite_mode
    )
    try:
        computed = compute_vis_range(mid_image, spec, roi)
    except Exception as exc:  # empty mid window falls back to catalog defaults
        if _is_empty_error(exc):
            computed = (spec.vis_min, spec.vis_max)
        else:
            raise
    lo = vis_min if vis_min is not None else computed[0]
    hi = vis_max if vis_max is not None else computed[1]
    return (lo, hi, None)


def _cleanup_staging(out_dir: Path) -> None:
    for pattern in (".staging_*.png", ".filled_*.png"):
        for staging in out_dir.glob(pattern):
            staging.unlink(missing_ok=True)


@dataclass(frozen=True)
class _WorkOut:
    """The concurrent fetch stage's result for one window (pre-finalisation)."""

    window: FrameWindow
    status: FrameStatus
    staging: Path | None  # annotated frame (legacy) or raw RGBA (post-processing path)
    source: str | None
    valid_fraction: float | None


def _load_rgba(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"))


def _save_rgba(arr: np.ndarray, path: Path) -> None:
    Image.fromarray(arr).save(path, format="PNG")  # (H, W, 4) uint8 → RGBA


def _resolve_ladder(
    dataset: str, product: str, spec: ProductSpec, fallback_source: str | None
) -> list[tuple[str, ProductSpec]]:
    """Primary source, then the fallback dataset when it carries the same product."""
    ladder: list[tuple[str, ProductSpec]] = [(dataset, spec)]
    if fallback_source is not None and fallback_source != dataset:
        try:
            fb_spec = get_dataset(fallback_source).get(product)
        except KeyError:
            return ladder  # fallback source lacks this product → no step-down
        if fb_spec.is_rgb == spec.is_rgb:
            ladder.append((fallback_source, fb_spec))
    return ladder


def render_frames(
    dataset: str,
    product: str,
    roi: ROI,
    windows: list[FrameWindow],
    *,
    out_dir: Path,
    max_dim: int,
    even_dims: bool,
    vis_min: float | None,
    vis_max: float | None,
    annotations: AnnotationOptions,
    composite_mode: CompositeMode = "mean",
    post: PostOptions | None = None,
    fallback_source: str | None = None,
    native_max_dim: int | None = None,
    fetch: FetchFn = _fetch_bytes,
    on_progress: Callable[[int, int], None] | None = None,
    on_frame: Callable[[int | None, FrameStatus, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> FrameManifest:
    """Render one PNG per *window* into *out_dir* and write ``manifest.json``.

    All frames share one geometry (even dimensions for video) and one vis range.
    Fully-auto RGB renders resolve that range by sampling a few windows (fix C);
    an HDR sequence (e.g. seasonal snow) additionally mints with the extended
    range and passes every frame through one fixed highlight-shoulder LUT
    (recorded as manifest ``tone``) so highlights keep texture without pumping.
    Per window: build the ``composite_mode`` composite (mean/median/clearest) over
    the per-window source ladder (primary → *fallback_source* on empty), mint the
    thumb through ``ee_call``, fetch the PNG, apply the requested *post*-processing
    (gap-fill → deflicker → grade → hole tint — all display-only, RGB frames only),
    then burn in the annotations. Empty composites are recorded ``empty`` and
    skipped; a non-PNG/failed fetch is recorded ``failed``. Rendered frames are
    re-indexed densely (``frame_0000.png`` = first rendered).

    ``on_progress(done, total)`` fires per completed window; ``on_frame(dense_index
    _or_None, status, total)`` fires per completed window with its movie index
    (``None`` when skipped) for live previews. The manifest records per-frame
    ``source``/``valid_fraction``/``filled_fraction`` and the ``composite``/``post``
    settings (hard rule 3) whatever mode runs; *native_max_dim* (the region's
    sensor limit, computed by the caller) is stored for the upscale readout.

    Back-compat (hard rule 2): with the defaults (mean, no post, no fallback) the
    frame builder annotates in the concurrent stage and the consumer ``os.replace``s
    each frame exactly as before — byte-identical output. Post-processing only
    engages the deferred-annotation path when a knob is actually set.

    Resilience: a window whose mint/fetch fails is recorded ``failed`` (not raised);
    the dead-pipeline breaker aborts only when EE is failing *consistently* (see
    :data:`EARLY_ABORT_PROBE`). On cancel, frames rendered so far are kept
    (``cancelled=True``); deflicker's finalisation pass still runs over them. Raises
    :class:`JobError` only when *nothing* rendered.
    """
    if not windows:
        raise JobError("Timelapse render needs at least one window.")

    post = post or PostOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_spec = get_dataset(dataset)
    spec = dataset_spec.get(product)
    product_is_rgb = spec.is_rgb

    # Honesty wall (hard rule 1): the artifact killers touch display frames only.
    if post.modifies_pixels() and not product_is_rgb:
        raise NonDisplayFrameError(
            f"Post-processing was requested on non-RGB product {dataset}/{product}; "
            "gap-fill / deflicker / grade / tint are display-only."
        )

    bbox = roi if isinstance(roi, BBox) else roi.bounds
    width, height = _frame_dimensions(bbox, max_dim, even_dims)
    dims_arg = f"{width}x{height}"
    vmin, vmax, knee_in = _resolve_vis_range(
        dataset, product, spec, roi, windows, vis_min, vis_max, composite_mode
    )
    # Fix C: an HDR RGB sequence mints with the extended range and every frame
    # passes through the same fixed shoulder LUT — no per-frame adaptation.
    tone_lut = highlight_shoulder_lut(knee_in) if knee_in is not None else None

    colorbar: Image.Image | None = None
    if annotations.colorbar and not product_is_rgb:
        cb_w = max(120, min(width // 4, 320))
        cb_h = max(18, round(height * 0.028))
        colorbar = render_colorbar(spec.palette, vmin, vmax, width=cb_w, height=cb_h)
    scale_bar = scale_bar_spec(bbox, width) if annotations.scale_bar else None
    attribution = annotations.attribution or dataset_spec.attribution

    ladder = _resolve_ladder(dataset, product, spec, fallback_source)
    defer = post.modifies_pixels() or tone_lut is not None  # raw frame before annotation
    second_pass = post.deflicker_strength > 0.0  # deflicker needs the whole sequence

    def _annotate(img: Image.Image, window: FrameWindow) -> Image.Image:
        label = window.label if annotations.date_label else ""
        return annotate_frame(
            img, label=label, attribution=attribution, colorbar=colorbar, scale_bar=scale_bar
        )

    def _fetch_display(window: FrameWindow) -> tuple[Image.Image, str]:
        """Fetch the raw display frame over the source ladder; raise on empty/fail."""
        for src, src_spec in ladder:
            try:
                image = build_composite(
                    product, roi, window.start, window.end, source=src, mode=composite_mode
                )
                url = thumb_url(
                    image, src_spec, roi, vis_min=vmin, vis_max=vmax, dimensions=dims_arg
                )
            except Exception as exc:
                if _is_empty_error(exc):
                    continue  # step down the ladder; empty-everywhere raises below
                raise
            data = fetch(url)
            if not data.startswith(_PNG_MAGIC):
                raise ValueError("EE returned a non-PNG payload")
            with Image.open(io.BytesIO(data)) as opened:
                base = opened.convert("RGBA")
            if base.size != (width, height):
                base = base.resize((width, height))
            return base, src
        raise EmptyCollectionError("no imagery for this window across the source ladder")

    def _finalize_and_save(arr: np.ndarray, window: FrameWindow, dest: Path) -> None:
        """grade → tint holes → annotate → save the final RGB frame."""
        if post.grade is not None and not post.grade.is_identity():
            arr = grade(arr, post.grade, product_is_rgb=product_is_rgb)
        if post.tint_hole_color is not None:
            arr = tint_holes(arr, post.tint_hole_color, product_is_rgb=product_is_rgb)
        annotated = _annotate(Image.fromarray(arr), window)  # (H, W, 4) uint8 → RGBA
        annotated.convert("RGB").save(dest, format="PNG")

    def _work(window: FrameWindow) -> _WorkOut:
        staging = out_dir / f".staging_{window.index:04d}.png"
        try:
            base, source = _fetch_display(window)
        except Exception as exc:  # empty → skip; anything else → failed (not raised)
            status: FrameStatus = "empty" if _is_empty_error(exc) else "failed"
            return _WorkOut(window, status, None, None, None)
        if tone_lut is not None:
            base = Image.fromarray(
                apply_lut(np.asarray(base), tone_lut, product_is_rgb=product_is_rgb)
            )
        vf = valid_fraction(np.asarray(base))
        if defer:
            base.save(staging, format="PNG")  # raw RGBA — annotate after post-processing
        else:
            _annotate(base, window).convert("RGB").save(staging, format="PNG")  # legacy path
        return _WorkOut(window, "rendered", staging, source, vf)

    filler = ForwardFiller(product_is_rgb=product_is_rgb) if post.gap_fill else None
    lumis: list[float | None] = []
    pending_second: list[tuple[Path, FrameWindow, int]] = []

    results: list[FrameResult] = []
    total = len(windows)
    dense = 0
    failed = 0
    cancelled = False
    with ThreadPoolExecutor(max_workers=FRAME_FETCH_WORKERS) as pool:
        futures = [pool.submit(_work, w) for w in windows]
        for i, fut in enumerate(futures):
            if should_cancel is not None and should_cancel():
                for pending in futures[i:]:
                    pending.cancel()
                cancelled = True
                break  # salvage what rendered so far (after the pool drains)
            out = fut.result()
            index: int | None = None
            if out.status == "rendered" and out.staging is not None:
                final = out_dir / f"frame_{dense:04d}.png"
                filled_fraction = 0.0
                if not defer:
                    os.replace(out.staging, final)  # already annotated — legacy path
                else:
                    arr = _load_rgba(out.staging)
                    out.staging.unlink(missing_ok=True)
                    if filler is not None:
                        arr, fill = filler.push(arr)  # forward-fill in window order
                        filled_fraction = fill.filled_fraction
                        if fill.mask is not None:  # fix D: dissolve the paste seam
                            arr = blend_fill_seams(arr, fill.mask, product_is_rgb=product_is_rgb)
                    if second_pass:
                        filled_staging = out_dir / f".filled_{dense:04d}.png"
                        _save_rgba(arr, filled_staging)
                        lumis.append(frame_luminance(arr))
                        pending_second.append((filled_staging, out.window, dense))
                    else:
                        _finalize_and_save(arr, out.window, final)
                results.append(
                    FrameResult(
                        out.window,
                        "rendered",
                        final,
                        out.source,
                        out.valid_fraction,
                        filled_fraction,
                    )
                )
                index = dense
                dense += 1
            else:
                results.append(
                    FrameResult(
                        out.window,
                        out.status,
                        None,
                        out.source,
                        out.valid_fraction,
                        0.0 if out.status == "empty" else None,
                    )
                )
                if out.status == "failed":
                    failed += 1
            if on_frame is not None:
                on_frame(index, out.status, total)
            if on_progress is not None:
                on_progress(i + 1, total)
            if len(results) == EARLY_ABORT_PROBE and dense == 0 and failed >= 1:
                for pending in futures[i + 1 :]:
                    pending.cancel()
                _cleanup_staging(out_dir)
                raise JobError(
                    f"Earth Engine failing consistently — aborted after {EARLY_ABORT_PROBE} "
                    "windows with no usable frame."
                )

    # Deflicker finalisation: with all frames staged, compute the gains against the
    # rolling luminance anchor and burn the final frames (runs over the salvaged
    # frames too on cancel).
    if pending_second:
        gains = deflicker_gains(lumis, strength=post.deflicker_strength)
        for k, (filled_staging, win, dense_idx) in enumerate(pending_second):
            arr = apply_gain(_load_rgba(filled_staging), gains[k], product_is_rgb=product_is_rgb)
            _finalize_and_save(arr, win, out_dir / f"frame_{dense_idx:04d}.png")
            filled_staging.unlink(missing_ok=True)

    _cleanup_staging(out_dir)

    if dense == 0:
        raise JobError(
            "cancelled"
            if cancelled
            else "Timelapse produced no usable frames (all windows empty or failed)."
        )

    post_manifest = post.to_manifest()
    post_manifest["fallback_source"] = fallback_source
    manifest = FrameManifest(
        dataset,
        product,
        width,
        height,
        (vmin, vmax),
        results,
        cancelled=cancelled,
        composite=composite_mode,
        post=post_manifest,
        native_max_dim=native_max_dim,
        tone=None
        if knee_in is None
        else {"knee_in": round(knee_in, 4), "knee_out": round(shoulder_knee_out(knee_in), 4)},
    )
    _write_manifest(out_dir / "manifest.json", manifest)
    return manifest


def _write_manifest(dest: Path, manifest: FrameManifest) -> None:
    """Write the manifest atomically (temp + os.replace)."""
    tmp = dest.parent / (dest.name + ".tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), indent=2))
    os.replace(tmp, dest)


# One output frame in the encode plan: ``Image.blend(open(a), open(b), alpha)``
# — ``alpha == 0`` (or ``a is b``) is the original frame ``a`` verbatim.
BlendStep = tuple[Path, Path, float]


def expand_frames(frame_paths: list[Path], tween: int) -> list[BlendStep]:
    """The encode plan for *tween* linear cross-fades between each consecutive pair.

    Between frame ``k`` and ``k+1`` it inserts *tween* blends at α = j/(tween+1),
    so the output length is ``len(frame_paths) + (len(frame_paths) - 1) * tween``.
    A display effect only — no new data. Pure (no image I/O), so the plan itself
    is unit-tested; the codec just executes it.
    """
    if tween <= 0 or len(frame_paths) < 2:
        return [(p, p, 0.0) for p in frame_paths]
    plan: list[BlendStep] = []
    for k in range(len(frame_paths) - 1):
        a, b = frame_paths[k], frame_paths[k + 1]
        plan.append((a, a, 0.0))  # original frame k
        for j in range(1, tween + 1):
            plan.append((a, b, j / (tween + 1)))
    plan.append((frame_paths[-1], frame_paths[-1], 0.0))  # final original frame
    return plan


def _plan_frame(step: BlendStep) -> Image.Image:
    """Realise one plan step into an RGB image (opening 1–2 frames, blending)."""
    a, b, alpha = step
    with Image.open(a) as ia:
        base: Image.Image = ia.convert("RGB")
    if alpha == 0.0 or a == b:
        return base
    with Image.open(b) as ib:
        other = ib.convert("RGB")
    return Image.blend(base, other, alpha)


def encode_movie(
    frame_paths: list[Path],
    out_path: Path,
    *,
    fmt: MovieFormat,
    fps: int,
    tween: int = 0,
) -> None:
    """Encode *frame_paths* into a movie at *out_path* (atomic temp + replace).

    mp4 → libx264/yuv420p, webm → libvpx-vp9, gif → Pillow. All frames must
    already share one exact size; video sizes must be even (guaranteed by
    :func:`render_frames`). A cancelled/crashed encode never leaves a truncated
    gallery item — the movie lands via ``os.replace``.

    *tween* inserts that many cross-faded frames between each consecutive pair
    (see :func:`expand_frames`); the encoder fps is scaled by ``tween + 1`` so
    wall-clock pacing is unchanged. The caller enforces any post-expansion frame
    cap (e.g. the GIF limit) — core encodes exactly the plan it is given.
    """
    if not frame_paths:
        raise JobError("Cannot encode a movie with no frames.")

    plan = expand_frames(frame_paths, tween)
    fps_out = fps * (tween + 1)

    # Keep the real extension on the temp file — ffmpeg picks the muxer from it.
    tmp = out_path.parent / f"{out_path.stem}.tmp{out_path.suffix}"
    try:
        if fmt == "gif":
            _encode_gif(plan, tmp, fps_out)
        else:
            _encode_video(plan, tmp, fmt=fmt, fps=fps_out)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, out_path)


def _encode_gif(plan: list[BlendStep], dest: Path, fps: int) -> None:
    # Pillow holds every frame in memory to write a GIF — MAX_DIM_GIF bounds it.
    frames = [_plan_frame(step) for step in plan]
    first, *rest = frames
    first.save(
        dest,
        format="GIF",
        save_all=True,
        append_images=rest,
        duration=round(1000 / fps),
        loop=0,
        optimize=True,
    )


# Constant-quality encode settings. imageio-ffmpeg's default quality=5 maps to
# x264 CRF 25 / vp9 -qscale 16 — visibly soft on satellite frames (a 12-frame
# 1080p render came out 172 KB). CRF 18 is the usual "visually lossless" x264
# point; vp9's scale differs, 30 with -b:v 0 selects its constant-quality mode.
X264_CRF = 18
VP9_CRF = 30


def _encode_video(plan: list[BlendStep], dest: Path, *, fmt: MovieFormat, fps: int) -> None:
    import imageio_ffmpeg

    size = _plan_frame(plan[0]).size  # (W, H) — all frames share it

    codec = "libx264" if fmt == "mp4" else "libvpx-vp9"
    crf_params = ["-crf", str(X264_CRF)] if fmt == "mp4" else ["-crf", str(VP9_CRF), "-b:v", "0"]
    try:
        # macro_block_size=1 disables imageio's pad-to-multiple-of-16 (our even
        # dims are already yuv420p-legal; padding would resample annotations).
        # quality=None keeps imageio-ffmpeg from emitting its own -crf/-qscale;
        # the explicit constants above are the only quality flags on the command.
        writer = imageio_ffmpeg.write_frames(
            str(dest),
            size,
            fps=fps,
            codec=codec,
            pix_fmt_out="yuv420p",
            macro_block_size=1,
            quality=None,
            output_params=crf_params,
        )
        writer.send(None)  # prime the generator
        for step in plan:
            writer.send(_plan_frame(step).tobytes())
        writer.close()
    except Exception as exc:  # surface ffmpeg's own stderr
        raise JobError(f"Movie encoding failed ({codec}): {exc}") from exc


# ── Phase 10 Stage 3: pacing, native-locked resolution, encode extras ──

# The 4K cap adopted after the Stage 0 spike proved getThumbURL serves 3840×2160.
MAX_DIM_4K = 3840
# Draft mode (decision 10): a fast, small preview at this longest edge.
DRAFT_MAX_DIM = 480
# The strength used when deflicker is toggled on as a boolean (API surfaces a
# switch, core takes a 0–1 strength).
DEFAULT_DEFLICKER_STRENGTH = 0.6
# Frames a title/end card is held for is derived from fps; this is the floor.
MIN_CARD_HOLD_FRAMES = 1

# Native ground sample distance per source (m) — the resolution lock (decision 9):
# a frame is never up-sampled past its sensor's native GSD.
NATIVE_GSD_M: dict[str, float] = {
    "s2": 10.0,
    "hls": 30.0,
    "landsat": 30.0,
    "s1": 10.0,
    "s5p": 1113.0,
    "emit": 60.0,
}
DEFAULT_GSD_M = 30.0

CropRatio = Literal["1:1", "9:16"]
CROP_RATIOS: dict[CropRatio, tuple[int, int]] = {"1:1": (1, 1), "9:16": (9, 16)}


def native_pixels(bbox: BBox, gsd_m: float) -> int:
    """Longest-edge native pixel count for *bbox* at *gsd_m* (cosine-corrected width)."""
    center_lat, _ = bbox.center
    width_m = abs(bbox.width_deg) * _M_PER_DEG * math.cos(math.radians(center_lat))
    height_m = abs(bbox.height_deg) * _M_PER_DEG
    return max(2, int(max(width_m, height_m) / max(gsd_m, 1e-6)))


def native_max_dim(bbox: BBox, dataset: str) -> int:
    """The resolution lock: the largest honest longest-edge for *dataset* over *bbox*."""
    return native_pixels(bbox, NATIVE_GSD_M.get(dataset, DEFAULT_GSD_M))


def plan_fps(n_frames: int, *, duration_s: float | None = None, fps: int | None = None) -> int:
    """Compile the two authoring modes to one fps (decision 8).

    Duration-first (``duration_s``) picks the fps that fits *n_frames* into the
    target seconds; frame-first uses *fps* directly. Result is clamped to [1, 30].
    """
    if duration_s is not None and duration_s > 0:
        return max(1, min(30, round(n_frames / duration_s)))
    return fps if fps is not None else 6


def center_crop_to_ratio(img: Image.Image, ratio_w: int, ratio_h: int) -> Image.Image:
    """Center-crop *img* to the *ratio_w*:*ratio_h* aspect, rounded to even dims."""
    w, h = img.size
    target = ratio_w / ratio_h
    if w / h > target:  # too wide — trim width
        new_w, new_h = round(h * target), h
    else:  # too tall — trim height
        new_w, new_h = w, round(w / target)
    left, top = (w - new_w) // 2, (h - new_h) // 2
    crop = img.crop((left, top, left + new_w, top + new_h))
    cw, ch = crop.size
    return crop.crop((0, 0, cw - cw % 2, ch - ch % 2))


def make_card(text: str, size: tuple[int, int], *, subtitle: str | None = None) -> Image.Image:
    """A declared title/end card: word-wrapped centred text on a near-black field."""
    w, h = size
    card = Image.new("RGB", (w, h), (12, 14, 18))
    draw = ImageDraw.Draw(card)
    title_size = max(16, round(h * 0.09))
    font = ImageFont.load_default(size=title_size)
    lines = _wrap_text(draw, text, font, int(w * 0.86))
    line_h = title_size + max(4, round(title_size * 0.25))
    block_h = line_h * len(lines)
    y = (h - block_h) // 2
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text(((w - tw) / 2, y), line, fill=(238, 240, 244), font=font)
        y += line_h
    if subtitle:
        sub_size = max(11, round(title_size * 0.5))
        sub_font = ImageFont.load_default(size=sub_size)
        sw = draw.textlength(subtitle, font=sub_font)
        draw.text(((w - sw) / 2, y + line_h // 2), subtitle, fill=(150, 156, 168), font=sub_font)
    return card


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def watermark_frame(img: Image.Image, text: str) -> Image.Image:
    """Composite a small semi-transparent *text* watermark at the bottom-right."""
    base = img.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    size = max(10, round(h * 0.03))
    font = ImageFont.load_default(size=size)
    tw = draw.textlength(text, font=font)
    pad = max(6, round(size * 0.6))
    x, y = w - tw - pad, h - size - pad
    draw.text((x + 1, y + 1), text, fill=(0, 0, 0, 140), font=font)  # shadow
    draw.text((x, y), text, fill=(255, 255, 255, 190), font=font)
    return Image.alpha_composite(base, overlay).convert("RGB")


def compose_extra_frames(
    frame_paths: list[Path],
    work_dir: Path,
    *,
    crop: CropRatio | None = None,
    watermark: str | None = None,
    title_card: str | None = None,
    end_card: str | None = None,
    card_hold: int = MIN_CARD_HOLD_FRAMES,
) -> list[Path]:
    """Materialise an extras frame sequence (cards + crop + watermark) in *work_dir*.

    Re-encodes from the kept frames only (never re-renders): optional intro/end
    cards bookend a per-frame transform of center-crop then watermark. Returns the
    new frame paths in order; the caller encodes them and removes *work_dir*.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    ratio = CROP_RATIOS[crop] if crop is not None else None

    def _transform(img: Image.Image) -> Image.Image:
        out = center_crop_to_ratio(img, *ratio) if ratio is not None else img.convert("RGB")
        return watermark_frame(out, watermark) if watermark else out.convert("RGB")

    with Image.open(frame_paths[0]) as first:
        size = _transform(first.convert("RGB")).size

    out: list[Path] = []
    counter = 0

    def _emit(image: Image.Image) -> None:
        nonlocal counter
        if image.size != size:
            image = image.resize(size)
        path = work_dir / f"x_{counter:05d}.png"
        image.convert("RGB").save(path, format="PNG")
        out.append(path)
        counter += 1

    if title_card:
        card = make_card(title_card, size)
        for _ in range(max(MIN_CARD_HOLD_FRAMES, card_hold)):
            _emit(card)
    for fp in frame_paths:
        with Image.open(fp) as im:
            _emit(_transform(im.convert("RGB")))
    if end_card:
        card = make_card(end_card, size)
        for _ in range(max(MIN_CARD_HOLD_FRAMES, card_hold)):
            _emit(card)
    return out


__all__ = [
    "CROP_RATIOS",
    "DEFAULT_DEFLICKER_STRENGTH",
    "DRAFT_MAX_DIM",
    "EARLY_ABORT_PROBE",
    "FRAME_FETCH_WORKERS",
    "MAX_DIM_4K",
    "MAX_DIM_GIF",
    "MAX_DIM_VIDEO",
    "MAX_FRAMES",
    "NATIVE_GSD_M",
    "AnnotationOptions",
    "BlendStep",
    "CropRatio",
    "FetchFn",
    "FrameManifest",
    "FrameResult",
    "FrameStatus",
    "FrameWindow",
    "MovieFormat",
    "PostOptions",
    "StepMode",
    "annotate_frame",
    "center_crop_to_ratio",
    "compose_extra_frames",
    "encode_movie",
    "expand_frames",
    "frame_windows",
    "make_card",
    "native_max_dim",
    "native_pixels",
    "plan_fps",
    "render_colorbar",
    "render_frames",
    "scale_bar_spec",
    "watermark_frame",
]
