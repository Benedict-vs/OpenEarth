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
    HIGHLIGHT_TRIGGER_RATIO,
    MAX_DEFLICKER_GAIN,
    SHOULDER_KNEE_OUT,
    ForwardFiller,
    GradeOptions,
    NonDisplayFrameError,
    apply_gain,
    apply_lut,
    deflicker,
    deflicker_gains,
    forward_fill,
    frame_luminance,
    grade,
    highlight_shoulder_lut,
    resolve_sequence_exposure,
    shoulder_knee_out,
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


# ── Sequence exposure + highlight shoulder (acceptance fix C) ────


def test_exposure_uniform_sequence_is_linear() -> None:
    # Five similar windows (Richmond Park): plain linear envelope, no shoulder.
    ranges = [(0.01, 0.30), (0.02, 0.32), (0.01, 0.29), (0.02, 0.31), (0.01, 0.30)]
    out = resolve_sequence_exposure(ranges, valid_min=0.0, valid_max=1.0)
    assert out is not None
    lo, hi, knee = out
    assert knee is None
    assert lo == 0.0  # p1 floor minus span headroom clamps at the physical floor
    assert hi == pytest.approx(0.32 + 0.31 * 0.05)  # headroom over the brightest p99


def test_exposure_snow_sequence_gets_a_shoulder() -> None:
    # Aletsch: summer windows peak ~0.3, winter snow ~0.8 → HDR → knee anchored
    # to the *typical* (25th-percentile) highlight, extended range to the snow.
    ranges = [(0.01, 0.80), (0.02, 0.30), (0.01, 0.78), (0.02, 0.32), (0.01, 0.85)]
    out = resolve_sequence_exposure(ranges, valid_min=0.0, valid_max=1.0)
    assert out is not None
    lo, hi, knee = out
    assert knee is not None
    assert hi > 0.85  # snow inside the minted range (plus headroom)
    # The knee sits where the typical window's highlight lands in the range.
    hi_typ = float(np.percentile([0.80, 0.30, 0.78, 0.32, 0.85], 25))
    assert knee == pytest.approx((hi_typ - lo) / (hi - lo))
    assert 0.1 <= knee < SHOULDER_KNEE_OUT


def test_exposure_one_bright_window_does_not_darken_the_sequence() -> None:
    # A single snowy window among normal ones must not become the linear top:
    # the trigger fires and midtones stay anchored to the typical highlight.
    ranges = [(0.0, 0.30)] * 4 + [(0.0, 0.90)]
    out = resolve_sequence_exposure(ranges, valid_min=0.0, valid_max=1.0)
    assert out is not None
    _, hi, knee = out
    assert knee is not None  # 0.9 > 1.25 × 0.3
    assert hi >= 0.90
    lut = highlight_shoulder_lut(knee)
    # A typical-window highlight (data 0.30 → t = knee) lands at the adaptive
    # knee-out — far brighter than the naive linear envelope would put it.
    expected = shoulder_knee_out(knee) * 255
    assert lut[round(knee * 255)] == pytest.approx(expected, abs=2.0)
    assert expected / 255 > knee  # brighter than plain linear


def test_exposure_ignores_empty_windows_and_none_when_all_empty() -> None:
    assert resolve_sequence_exposure([None, None], valid_min=0.0, valid_max=1.0) is None
    out = resolve_sequence_exposure([None, (0.0, 0.3), None], valid_min=0.0, valid_max=1.0)
    assert out is not None
    assert out[2] is None  # single window → nothing to compare → linear


def test_exposure_clamps_to_valid_range() -> None:
    ranges = [(-0.10, 0.95), (0.0, 0.98)]
    out = resolve_sequence_exposure(ranges, valid_min=0.0, valid_max=1.0)
    assert out is not None
    lo, hi, _ = out
    assert lo == 0.0  # negative reflectance tail clamped
    assert hi == 1.0  # headroom clamped to the physical ceiling


def test_exposure_trigger_ratio_boundary() -> None:
    # Exactly at the trigger: no shoulder. Just past it: shoulder engages.
    # (Three identical typical windows pin the 25th percentile at 1.0.)
    at = [(0.0, 1.0)] * 3 + [(0.0, HIGHLIGHT_TRIGGER_RATIO)]
    past = [(0.0, 1.0)] * 3 + [(0.0, HIGHLIGHT_TRIGGER_RATIO + 0.1)]
    out_at = resolve_sequence_exposure(at, valid_min=0.0, valid_max=2.0)
    out_past = resolve_sequence_exposure(past, valid_min=0.0, valid_max=2.0)
    assert out_at is not None
    assert out_at[2] is None
    assert out_past is not None
    assert out_past[2] is not None


def test_shoulder_lut_is_monotone_with_exact_endpoints() -> None:
    lut = highlight_shoulder_lut(0.35)
    assert lut.shape == (256,)
    assert lut[0] == 0
    assert lut[255] == 255
    assert np.all(np.diff(lut.astype(np.int16)) >= 0)


def test_shoulder_lut_linear_below_knee_compressed_above() -> None:
    knee = 0.4
    q = shoulder_knee_out(knee)  # 3·0.4/1.8 = 2/3 (the slope-ratio bound binds)
    assert q == pytest.approx(2 / 3)
    lut = highlight_shoulder_lut(knee)
    # Below the knee the slope is knee_out/knee_in > 1 (midtones keep contrast)…
    assert lut[round(0.2 * 255)] == pytest.approx(0.2 * (q / knee) * 255, abs=1.5)
    # …the knee itself maps to the knee-out…
    assert lut[round(knee * 255)] == pytest.approx(q * 255, abs=2.0)
    # …and the shoulder keeps real gradation: mid-shoulder is clearly below 255.
    assert lut[round(0.7 * 255)] < 250
    assert lut[round(0.7 * 255)] > lut[round(knee * 255)]


def test_shoulder_knee_out_caps_at_max() -> None:
    # A high knee doesn't need the slope bound — the 0.85 ceiling applies.
    assert shoulder_knee_out(0.75) == pytest.approx(SHOULDER_KNEE_OUT)
    # A low knee is bound by the slope ratio, keeping shoulder texture.
    assert shoulder_knee_out(0.2) == pytest.approx(0.6 / 1.4)


def test_shoulder_lut_rejects_bad_knees() -> None:
    for bad in (0.0, 0.9, 1.0):
        with pytest.raises(ValueError, match="knee"):
            highlight_shoulder_lut(bad)
    with pytest.raises(ValueError, match="knee"):
        highlight_shoulder_lut(0.5, knee_out=0.4)  # knee_out must exceed knee_in


def test_apply_lut_touches_rgb_only_and_guards_display() -> None:
    lut = highlight_shoulder_lut(0.35)
    frame = _px(100, 150, 200, 42)
    out = apply_lut(frame, lut, product_is_rgb=True)
    assert out[0, 0, 3] == 42  # alpha untouched
    assert list(out[0, 0, :3]) == [lut[100], lut[150], lut[200]]
    with pytest.raises(NonDisplayFrameError):
        apply_lut(frame, lut, product_is_rgb=False)
    with pytest.raises(ValueError, match="256"):
        apply_lut(frame, lut[:100], product_is_rgb=True)


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
