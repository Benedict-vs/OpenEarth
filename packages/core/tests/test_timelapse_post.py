"""Pure post-processing layer: gap-fill, deflicker, grade, tint, QC — Stage 2's heart.

All synthetic RGBA sequences, zero Earth Engine. Covers the declared physics
(fill staleness cap, deflicker gain clamp, grade curve monotonicity + slider
bounds), exact honesty stats, and the honesty-wall refusal on non-RGB products.
"""

from __future__ import annotations

import numpy as np
import pytest

from openearth.timelapse_post import (
    CURVES,
    FILL_CAP_WINDOWS,
    MAX_DEFLICKER_GAIN,
    ForwardFiller,
    GradeOptions,
    NonDisplayFrameError,
    apply_gain,
    deflicker,
    deflicker_gains,
    forward_fill,
    frame_luminance,
    grade,
    tint_holes,
    valid_fraction,
)


def _px(r: int, g: int, b: int, a: int = 255) -> np.ndarray:
    """A 1×1 RGBA frame."""
    return np.array([[[r, g, b, a]]], dtype=np.uint8)


def _hole() -> np.ndarray:
    return _px(0, 0, 0, 0)


# ── Honesty measurements ─────────────────────────────────────────


def test_valid_fraction_counts_opaque_pixels() -> None:
    frame = np.zeros((2, 2, 4), dtype=np.uint8)
    frame[0, 0, 3] = 255
    frame[0, 1, 3] = 255
    assert valid_fraction(frame) == 0.5


def test_frame_luminance_ignores_holes_and_returns_none_when_empty() -> None:
    frame = np.zeros((1, 2, 4), dtype=np.uint8)
    frame[0, 0] = [200, 200, 200, 255]  # luminance 200
    frame[0, 1] = [10, 10, 10, 0]  # a hole — excluded
    assert frame_luminance(frame) == pytest.approx(200.0, abs=0.6)
    assert frame_luminance(_hole()) is None


# ── Gap-fill (decision 3) ────────────────────────────────────────


def test_forward_fill_honors_the_staleness_cap() -> None:
    # Valid once, then four consecutive holes. cap=2 fills the first two holes;
    # the 3-window-old hole and beyond stay holes.
    seq = [_px(10, 20, 30), _hole(), _hole(), _hole(), _hole()]
    filled, fills = forward_fill(seq, cap_windows=2, product_is_rgb=True)

    assert filled[0][0, 0, 3] == 255  # original observation
    # windows 1 and 2 inherit the last valid pixel
    for k in (1, 2):
        assert list(filled[k][0, 0]) == [10, 20, 30, 255]
        assert fills[k].filled_fraction == 1.0
    assert fills[1].max_staleness == 1
    assert fills[2].max_staleness == 2
    # window 3 is 3 windows stale (> cap) → stays a hole
    assert filled[3][0, 0, 3] == 0
    assert fills[3].filled_fraction == 0.0
    assert fills[3].max_staleness == 0
    assert filled[4][0, 0, 3] == 0


def test_forward_fill_partial_frame_masks() -> None:
    # 1×2 frame: left pixel valid then hole; right pixel always a hole.
    f0 = np.array([[[10, 10, 10, 255], [0, 0, 0, 0]]], dtype=np.uint8)
    f1 = np.array([[[0, 0, 0, 0], [0, 0, 0, 0]]], dtype=np.uint8)
    filled, fills = forward_fill([f0, f1], cap_windows=2, product_is_rgb=True)
    assert list(filled[1][0, 0]) == [10, 10, 10, 255]  # left filled
    assert filled[1][0, 1, 3] == 0  # right never observed → stays hole
    assert fills[1].filled_fraction == 0.5


def test_forward_fill_default_cap_is_two_windows() -> None:
    assert FILL_CAP_WINDOWS == 2


def test_forward_filler_streaming_matches_whole_sequence() -> None:
    seq = [_px(50, 60, 70), _hole(), _px(80, 90, 100), _hole(), _hole()]
    whole, whole_fills = forward_fill(seq, product_is_rgb=True)
    filler = ForwardFiller(product_is_rgb=True)
    for i, frame in enumerate(seq):
        out, info = filler.push(frame)
        np.testing.assert_array_equal(out, whole[i])
        assert info == whole_fills[i]


# ── Deflicker (decision 4) ───────────────────────────────────────


def test_deflicker_gain_clamped_and_centered() -> None:
    # A single dark frame between bright ones: the rolling median is bright, so the
    # gain pushes it up but is clamped to +MAX_DEFLICKER_GAIN.
    lums = [100.0, 100.0, 40.0, 100.0, 100.0]
    gains = deflicker_gains(lums, strength=1.0)
    assert gains[2] == pytest.approx(1.0 + MAX_DEFLICKER_GAIN)  # 100/40=2.5 → clamped
    assert gains[0] == pytest.approx(1.0)  # already at the reference


def test_deflicker_strength_scales_correction() -> None:
    lums = [100.0, 80.0, 100.0]
    full = deflicker_gains(lums, strength=1.0)[1]
    half = deflicker_gains(lums, strength=0.5)[1]
    off = deflicker_gains(lums, strength=0.0)[1]
    assert off == pytest.approx(1.0)
    # raw gain 100/80 = 1.25 → clamped to 1.2 at full strength; half (1.125) is between.
    assert full == pytest.approx(1.0 + MAX_DEFLICKER_GAIN)
    assert 1.0 < half < full


def test_deflicker_gain_one_for_empty_frames() -> None:
    assert deflicker_gains([100.0, None, 100.0], strength=1.0)[1] == 1.0


def test_apply_gain_scales_rgb_clips_and_keeps_alpha() -> None:
    out = apply_gain(_px(100, 100, 100, 200), 1.2, product_is_rgb=True)
    assert list(out[0, 0]) == [120, 120, 120, 200]
    clipped = apply_gain(_px(200, 200, 200), 3.0, product_is_rgb=True)
    assert list(clipped[0, 0][:3]) == [255, 255, 255]


def test_deflicker_whole_sequence_no_op_at_zero_strength() -> None:
    seq = [_px(120, 60, 30), _px(40, 200, 100), _px(10, 10, 250)]
    out = deflicker(seq, strength=0.0, product_is_rgb=True)
    for a, b in zip(seq, out, strict=True):
        np.testing.assert_array_equal(a, b)


# ── Grade (decision 5) ───────────────────────────────────────────


@pytest.mark.parametrize("curve", ["natural", "vivid", "cinematic"])
def test_grade_curves_are_monotonic(curve: str) -> None:
    lut = CURVES[curve]
    assert lut.shape == (256,)
    assert np.all(np.diff(lut.astype(np.int16)) >= 0)


def test_natural_grade_is_identity() -> None:
    frame = _px(37, 128, 201, 128)
    out = grade(frame, GradeOptions(), product_is_rgb=True)
    np.testing.assert_array_equal(out, frame)
    assert GradeOptions().is_identity() is True
    assert GradeOptions(curve="vivid").is_identity() is False


def test_grade_saturation_zero_is_greyscale() -> None:
    out = grade(_px(200, 50, 50), GradeOptions(saturation=0.0), product_is_rgb=True)
    r, g, b, _ = out[0, 0]
    assert r == g == b  # collapsed to luminance


def test_grade_rejects_out_of_range_sliders() -> None:
    frame = _px(100, 100, 100)
    for bad in (
        GradeOptions(saturation=2.5),
        GradeOptions(brightness=1.5),
        GradeOptions(contrast=-2.0),
    ):
        with pytest.raises(ValueError, match="must be in"):
            grade(frame, bad, product_is_rgb=True)


# ── Tint holes (Survey honesty) ──────────────────────────────────


def test_tint_holes_paints_gaps_and_leaves_data() -> None:
    frame = np.array([[[10, 20, 30, 255], [0, 0, 0, 0]]], dtype=np.uint8)
    out = tint_holes(frame, (255, 0, 255), product_is_rgb=True)
    assert list(out[0, 0]) == [10, 20, 30, 255]  # data untouched
    assert list(out[0, 1]) == [255, 0, 255, 255]  # hole flagged opaque


# ── Honesty wall (hard rule 1) ───────────────────────────────────


def test_modifiers_refuse_non_rgb_products() -> None:
    frame = _px(100, 100, 100)
    seq = [frame, _hole()]
    with pytest.raises(NonDisplayFrameError):
        ForwardFiller(product_is_rgb=False)
    with pytest.raises(NonDisplayFrameError):
        forward_fill(seq, product_is_rgb=False)
    with pytest.raises(NonDisplayFrameError):
        apply_gain(frame, 1.1, product_is_rgb=False)
    with pytest.raises(NonDisplayFrameError):
        deflicker(seq, strength=1.0, product_is_rgb=False)
    with pytest.raises(NonDisplayFrameError):
        grade(frame, GradeOptions(curve="vivid"), product_is_rgb=False)
    with pytest.raises(NonDisplayFrameError):
        tint_holes(frame, (255, 0, 255), product_is_rgb=False)


def test_measurements_have_no_display_guard() -> None:
    # valid_fraction / frame_luminance are honesty surfaces, recorded for every
    # product (hard rule 3) — they carry no product_is_rgb guard at all.
    frame = _px(123, 200, 50)
    assert 0.0 <= valid_fraction(frame) <= 1.0
    assert frame_luminance(frame) is not None
