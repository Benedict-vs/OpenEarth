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

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from openearth.geometry import BBox

# One degree of latitude in metres (WGS84 mean) — the same convention the
# pixel grid math and the web-side geo box use. Kept local so the pure layer
# doesn't drag in Earth Engine just to reach a constant.
_M_PER_DEG = 111_320.0

# ── Frame-budget constants ───────────────────────────────────────
MAX_FRAMES = 400  # request 422s above this before any EE work
MAX_DIM_VIDEO = 1920  # longest edge, mp4/webm
MAX_DIM_GIF = 720  # Pillow holds every GIF frame in RAM
FRAME_FETCH_WORKERS = 4  # ThreadPoolExecutor width (EE semaphore still gates round-trips)

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
    """En-dashed range for multi-day windows; the single date when one day."""
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} – {end.isoformat()}"


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


__all__ = [
    "FRAME_FETCH_WORKERS",
    "MAX_DIM_GIF",
    "MAX_DIM_VIDEO",
    "MAX_FRAMES",
    "FrameWindow",
    "StepMode",
    "annotate_frame",
    "frame_windows",
    "render_colorbar",
    "scale_bar_spec",
]
