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

from PIL import Image, ImageDraw, ImageFont

from openearth.catalog import get_dataset
from openearth.composites import build_mean_composite
from openearth.ee.render import compute_vis_range, geo_dimensions, thumb_url
from openearth.errors import EmptyCollectionError, JobError, classify_ee_error
from openearth.geometry import BBox

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
class FrameResult:
    """The outcome for one window: a rendered PNG on disk, empty, or failed."""

    window: FrameWindow
    status: FrameStatus
    path: Path | None


@dataclass(frozen=True)
class FrameManifest:
    """Everything a movie encoder and the gallery need about a render."""

    dataset: str
    product: str
    width: int
    height: int
    vis: tuple[float, float]
    results: list[FrameResult] = field(default_factory=list)

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
                }
            )
        return {
            "dataset": self.dataset,
            "product": self.product,
            "width": self.width,
            "height": self.height,
            "vis": [self.vis[0], self.vis[1]],
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


def _resolve_vis_range(
    dataset: str,
    product: str,
    spec: ProductSpec,
    roi: ROI,
    windows: list[FrameWindow],
    vis_min: float | None,
    vis_max: float | None,
) -> tuple[float, float]:
    """One vis range for the whole render (no per-frame auto-scale flicker).

    Uses the request overrides where given, else ``compute_vis_range`` on the
    middle window's composite — computed once and reused for every frame and
    the colorbar.
    """
    if vis_min is not None and vis_max is not None:
        return (vis_min, vis_max)

    mid = windows[len(windows) // 2]
    mid_image = build_mean_composite(product, roi, mid.start, mid.end, source=dataset)
    try:
        computed = compute_vis_range(mid_image, spec, roi)
    except Exception as exc:  # empty mid window falls back to catalog defaults
        if _is_empty_error(exc):
            computed = (spec.vis_min, spec.vis_max)
        else:
            raise
    lo = vis_min if vis_min is not None else computed[0]
    hi = vis_max if vis_max is not None else computed[1]
    return (lo, hi)


def _cleanup_staging(out_dir: Path) -> None:
    for staging in out_dir.glob(".staging_*.png"):
        staging.unlink(missing_ok=True)


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
    fetch: FetchFn = _fetch_bytes,
    on_progress: Callable[[int, int], None] | None = None,
    on_frame: Callable[[int | None, FrameStatus, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> FrameManifest:
    """Render one PNG per *window* into *out_dir* and write ``manifest.json``.

    All frames share one geometry (even dimensions for video) and one vis range.
    Per window: build the mean composite, mint the thumb through ``ee_call``,
    fetch the PNG, then burn in the annotations. Empty composites are recorded
    as ``empty`` and skipped; a non-PNG/failed fetch is recorded as ``failed``.
    Rendered frames are re-indexed densely *as they complete* (``frame_0000.png``
    = first rendered) so the movie has no holes and the API can serve each frame
    live. ``on_progress(done, total)`` fires per completed window (generic bar);
    ``on_frame(dense_index_or_None, status, total)`` fires per completed window
    with its assigned movie index (``None`` when skipped) for live previews.
    Raises :class:`JobError` only if *nothing* rendered, or ``"cancelled"`` when
    *should_cancel* trips between frames.
    """
    if not windows:
        raise JobError("Timelapse render needs at least one window.")

    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_spec = get_dataset(dataset)
    spec = dataset_spec.get(product)
    bbox = roi if isinstance(roi, BBox) else roi.bounds

    width, height = _frame_dimensions(bbox, max_dim, even_dims)
    dims_arg = f"{width}x{height}"
    vmin, vmax = _resolve_vis_range(dataset, product, spec, roi, windows, vis_min, vis_max)

    colorbar: Image.Image | None = None
    if annotations.colorbar and not spec.is_rgb:
        cb_w = max(120, min(width // 4, 320))
        cb_h = max(18, round(height * 0.028))
        colorbar = render_colorbar(spec.palette, vmin, vmax, width=cb_w, height=cb_h)
    scale_bar = scale_bar_spec(bbox, width) if annotations.scale_bar else None
    attribution = annotations.attribution or dataset_spec.attribution

    def _work(window: FrameWindow) -> FrameResult:
        staging = out_dir / f".staging_{window.index:04d}.png"
        try:
            image = build_mean_composite(product, roi, window.start, window.end, source=dataset)
            url = thumb_url(image, spec, roi, vis_min=vmin, vis_max=vmax, dimensions=dims_arg)
        except Exception as exc:  # empty → skip; anything else re-raises
            if _is_empty_error(exc):
                return FrameResult(window, "empty", None)
            raise
        try:
            data = fetch(url)
        except Exception:  # fetch failures are recorded, not raised
            return FrameResult(window, "failed", None)
        if not data.startswith(_PNG_MAGIC):
            return FrameResult(window, "failed", None)

        with Image.open(io.BytesIO(data)) as opened:
            base = opened.convert("RGBA")
        if base.size != (width, height):
            # EE should honour the explicit WxH; resize the base (never the
            # annotated frame) as a safety net so every frame matches the movie.
            base = base.resize((width, height))
        label = window.label if annotations.date_label else ""
        annotated = annotate_frame(
            base, label=label, attribution=attribution, colorbar=colorbar, scale_bar=scale_bar
        )
        annotated.convert("RGB").save(staging, format="PNG")
        return FrameResult(window, "rendered", staging)

    # Frames run concurrently but results are consumed in window order, so each
    # rendered frame's dense index is settled the moment it lands — no post-pass.
    results: list[FrameResult] = []
    total = len(windows)
    dense = 0
    with ThreadPoolExecutor(max_workers=FRAME_FETCH_WORKERS) as pool:
        futures = [pool.submit(_work, w) for w in windows]
        for i, fut in enumerate(futures):
            if should_cancel is not None and should_cancel():
                for pending in futures[i:]:
                    pending.cancel()
                _cleanup_staging(out_dir)
                raise JobError("cancelled")
            r = fut.result()
            index: int | None = None
            if r.status == "rendered" and r.path is not None:
                final = out_dir / f"frame_{dense:04d}.png"
                os.replace(r.path, final)
                results.append(FrameResult(r.window, "rendered", final))
                index = dense
                dense += 1
            else:
                results.append(FrameResult(r.window, r.status, None))
            if on_frame is not None:
                on_frame(index, r.status, total)
            if on_progress is not None:
                on_progress(i + 1, total)

    if dense == 0:
        raise JobError("Timelapse produced no usable frames (all windows empty or failed).")

    manifest = FrameManifest(dataset, product, width, height, (vmin, vmax), results)
    _write_manifest(out_dir / "manifest.json", manifest)
    return manifest


def _write_manifest(dest: Path, manifest: FrameManifest) -> None:
    """Write the manifest atomically (temp + os.replace)."""
    tmp = dest.parent / (dest.name + ".tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), indent=2))
    os.replace(tmp, dest)


def encode_movie(
    frame_paths: list[Path],
    out_path: Path,
    *,
    fmt: MovieFormat,
    fps: int,
) -> None:
    """Encode *frame_paths* into a movie at *out_path* (atomic temp + replace).

    mp4 → libx264/yuv420p, webm → libvpx-vp9, gif → Pillow. All frames must
    already share one exact size; video sizes must be even (guaranteed by
    :func:`render_frames`). A cancelled/crashed encode never leaves a truncated
    gallery item — the movie lands via ``os.replace``.
    """
    if not frame_paths:
        raise JobError("Cannot encode a movie with no frames.")

    # Keep the real extension on the temp file — ffmpeg picks the muxer from it.
    tmp = out_path.parent / f"{out_path.stem}.tmp{out_path.suffix}"
    try:
        if fmt == "gif":
            _encode_gif(frame_paths, tmp, fps)
        else:
            _encode_video(frame_paths, tmp, fmt=fmt, fps=fps)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, out_path)


def _encode_gif(frame_paths: list[Path], dest: Path, fps: int) -> None:
    # Pillow holds every frame in memory to write a GIF — MAX_DIM_GIF bounds it.
    frames = [Image.open(p).convert("RGB") for p in frame_paths]
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


def _encode_video(frame_paths: list[Path], dest: Path, *, fmt: MovieFormat, fps: int) -> None:
    import imageio_ffmpeg

    with Image.open(frame_paths[0]) as first:
        size = first.size  # (W, H) — all frames share it

    codec = "libx264" if fmt == "mp4" else "libvpx-vp9"
    try:
        # macro_block_size=1 disables imageio's pad-to-multiple-of-16 (our even
        # dims are already yuv420p-legal; padding would resample annotations).
        writer = imageio_ffmpeg.write_frames(
            str(dest),
            size,
            fps=fps,
            codec=codec,
            pix_fmt_out="yuv420p",
            macro_block_size=1,
        )
        writer.send(None)  # prime the generator
        for path in frame_paths:
            with Image.open(path) as frame:
                writer.send(frame.convert("RGB").tobytes())
        writer.close()
    except Exception as exc:  # surface ffmpeg's own stderr
        raise JobError(f"Movie encoding failed ({codec}): {exc}") from exc


__all__ = [
    "FRAME_FETCH_WORKERS",
    "MAX_DIM_GIF",
    "MAX_DIM_VIDEO",
    "MAX_FRAMES",
    "AnnotationOptions",
    "FetchFn",
    "FrameManifest",
    "FrameResult",
    "FrameStatus",
    "FrameWindow",
    "MovieFormat",
    "StepMode",
    "annotate_frame",
    "encode_movie",
    "frame_windows",
    "render_colorbar",
    "render_frames",
    "scale_bar_spec",
]
