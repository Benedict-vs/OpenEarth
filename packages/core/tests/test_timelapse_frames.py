"""Stage 1 pure layer: frame stepping + Pillow annotation helpers (offline)."""

from __future__ import annotations

import dataclasses
import math
from datetime import date, timedelta

import pytest
from PIL import Image

from openearth.geometry import BBox
from openearth.timelapse import (
    MAX_FRAMES,
    FrameWindow,
    annotate_frame,
    frame_windows,
    render_colorbar,
    scale_bar_spec,
)

# ── frame_windows: interval ──────────────────────────────────────


def test_interval_basic_stepping() -> None:
    windows = frame_windows(date(2024, 1, 1), date(2024, 2, 1), mode="interval", interval_days=16)
    # 2024-01-01, 2024-01-17, 2024-02-02(>end→last starts 2024-01-17..? ) —
    # windows open at day 0, 16, 32; day 32 = 2024-02-02 is past end → 2 windows.
    assert [w.index for w in windows] == [0, 1]
    assert windows[0].start == date(2024, 1, 1)
    assert windows[0].end == date(2024, 1, 16)  # 16-day span, inclusive
    assert windows[1].start == date(2024, 1, 17)
    assert windows[1].end == date(2024, 2, 1)  # clipped to end


def test_interval_single_day_label() -> None:
    windows = frame_windows(
        date(2024, 1, 1), date(2024, 1, 3), mode="interval", interval_days=1, window_days=1
    )
    assert all(w.start == w.end for w in windows)
    assert windows[0].label == "2024-01-01"  # single day → bare date


def test_interval_multiday_label_is_ascii_only() -> None:
    # ASCII hyphen, not an en dash: the burn-in font has no en-dash glyph.
    windows = frame_windows(date(2024, 1, 1), date(2024, 1, 20), mode="interval", interval_days=16)
    assert windows[0].label == "2024-01-01 - 2024-01-16"
    assert windows[0].label.isascii()


def test_interval_rolling_overlap_when_window_exceeds_interval() -> None:
    windows = frame_windows(
        date(2024, 1, 1), date(2024, 3, 1), mode="interval", interval_days=10, window_days=20
    )
    # window 1 opens at day 10 but window 0 spans 20 days → they overlap.
    assert windows[0].end > windows[1].start


# ── frame_windows: monthly ───────────────────────────────────────


def test_monthly_clips_both_ends() -> None:
    windows = frame_windows(date(2024, 1, 15), date(2024, 3, 10), mode="monthly")
    assert [w.label for w in windows] == ["2024-01", "2024-02", "2024-03"]
    assert windows[0].start == date(2024, 1, 15)  # clipped to start
    assert windows[0].end == date(2024, 1, 31)
    assert windows[-1].end == date(2024, 3, 10)  # clipped to end


def test_monthly_february_leap_year() -> None:
    windows = frame_windows(date(2024, 2, 1), date(2024, 2, 29), mode="monthly")
    assert len(windows) == 1
    assert windows[0].end == date(2024, 2, 29)  # 2024 is a leap year


def test_monthly_february_non_leap_year() -> None:
    windows = frame_windows(date(2023, 2, 1), date(2023, 3, 1), mode="monthly")
    assert windows[0].end == date(2023, 2, 28)  # 2023 not a leap year


def test_monthly_31_to_30_boundary() -> None:
    # March (31 days) into April (30 days): each month's end is correct.
    windows = frame_windows(date(2024, 3, 20), date(2024, 4, 20), mode="monthly")
    assert windows[0].end == date(2024, 3, 31)
    assert windows[1].start == date(2024, 4, 1)


# ── frame_windows: quarterly ─────────────────────────────────────


def test_quarterly_labels_and_clipping() -> None:
    windows = frame_windows(date(2024, 2, 1), date(2024, 8, 15), mode="quarterly")
    assert [w.label for w in windows] == ["2024-Q1", "2024-Q2", "2024-Q3"]
    assert windows[0].start == date(2024, 2, 1)  # clipped to start (Q1 begins Jan 1)
    assert windows[0].end == date(2024, 3, 31)
    assert windows[1].start == date(2024, 4, 1)
    assert windows[-1].end == date(2024, 8, 15)  # clipped to end


def test_quarterly_crosses_year_boundary() -> None:
    windows = frame_windows(date(2023, 11, 1), date(2024, 2, 1), mode="quarterly")
    assert [w.label for w in windows] == ["2023-Q4", "2024-Q1"]


# ── frame_windows: validation ────────────────────────────────────


def test_end_before_start_raises() -> None:
    with pytest.raises(ValueError, match="must not be before"):
        frame_windows(date(2024, 2, 1), date(2024, 1, 1), mode="interval")


def test_non_positive_interval_raises() -> None:
    with pytest.raises(ValueError, match="interval_days must be positive"):
        frame_windows(date(2024, 1, 1), date(2024, 2, 1), mode="interval", interval_days=0)


def test_non_positive_window_days_raises() -> None:
    with pytest.raises(ValueError, match="window_days must be positive"):
        frame_windows(
            date(2024, 1, 1), date(2024, 2, 1), mode="interval", interval_days=5, window_days=-1
        )


def test_too_many_frames_raises() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        frame_windows(date(2000, 1, 1), date(2010, 1, 1), mode="interval", interval_days=1)


def test_exactly_max_frames_ok() -> None:
    # interval_days=1 → MAX_FRAMES windows over MAX_FRAMES-1 days span.
    windows = frame_windows(
        date(2024, 1, 1),
        date(2024, 1, 1) + timedelta(days=MAX_FRAMES - 1),
        mode="interval",
        interval_days=1,
        window_days=1,
    )
    assert len(windows) == MAX_FRAMES


# ── scale_bar_spec ───────────────────────────────────────────────


def test_scale_bar_fits_within_quarter_width() -> None:
    # ~1° box near the equator ≈ 111 km wide; 25% ≈ 27.8 km → 20 km bar.
    bbox = BBox(0.0, 0.0, 1.0, 1.0)
    km, px = scale_bar_spec(bbox, width_px=1000)
    assert km == 20.0
    # px must correspond to km at the frame's m/px and be ≤ 25% of width.
    assert px <= 250
    assert px > 0


def test_scale_bar_is_nice_number() -> None:
    bbox = BBox(0.0, 0.0, 0.1, 0.1)
    km, _ = scale_bar_spec(bbox, width_px=800)
    mantissa = km / 10 ** math.floor(math.log10(km))
    assert round(mantissa, 6) in (1.0, 2.0, 5.0)


def test_scale_bar_cosine_correction_shrinks_high_latitude_bar() -> None:
    # Same degree-width box: at 60°N the ground width halves (cos60=0.5), so
    # the chosen bar length must be no larger than the equatorial choice.
    equator = scale_bar_spec(BBox(0.0, 0.0, 2.0, 2.0), 1000)[0]
    high_lat = scale_bar_spec(BBox(0.0, 59.0, 2.0, 61.0), 1000)[0]
    assert high_lat <= equator


# ── render_colorbar / annotate_frame ─────────────────────────────

PALETTE = ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"]


def test_render_colorbar_size_and_mode() -> None:
    cb = render_colorbar(PALETTE, 0.0, 100.0, width=200, height=40)
    assert cb.size == (200, 40)
    assert cb.mode == "RGBA"
    # The gradient's leftmost pixel matches the first palette colour.
    r, g, b, _ = cb.getpixel((0, 0))
    assert (r, g, b) == (0, 0, 4)


def test_annotate_frame_preserves_size_and_composites_strip() -> None:
    frame = Image.new("RGB", (320, 240), (100, 150, 200))
    cb = render_colorbar(PALETTE, 0.0, 1.0, width=120, height=18)
    out = annotate_frame(
        frame,
        label="2024-01",
        attribution="Google Earth Engine",
        colorbar=cb,
        scale_bar=(10.0, 60),
    )
    assert out.size == (320, 240)
    assert out.mode == "RGBA"
    # The bottom strip is darkened relative to the flat blue original.
    top_pixel = out.getpixel((10, 10))
    bottom_pixel = out.getpixel((10, 235))
    assert sum(bottom_pixel[:3]) < sum(top_pixel[:3])


def test_annotate_frame_all_annotations_optional() -> None:
    frame = Image.new("RGB", (200, 120), (50, 50, 50))
    out = annotate_frame(frame, label="", attribution="", colorbar=None, scale_bar=None)
    assert out.size == (200, 120)


def test_frame_window_is_frozen() -> None:
    w = FrameWindow(0, date(2024, 1, 1), date(2024, 1, 2), "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.index = 5  # type: ignore[misc]
