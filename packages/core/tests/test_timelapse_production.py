"""Phase 10 Stage 3 core helpers: pacing, native-locked resolution, encode extras."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from openearth.geometry import BBox
from openearth.timelapse import (
    MAX_DIM_4K,
    center_crop_to_ratio,
    compose_extra_frames,
    make_card,
    native_max_dim,
    native_pixels,
    plan_fps,
    watermark_frame,
)

RICHMOND_PARK = BBox(-0.30, 51.42, -0.25, 51.46)  # ~3.5 × 4.4 km
PO_VALLEY = BBox(8.5, 44.7, 11.5, 45.6)  # large — native exceeds the 4K cap


def test_native_resolution_locks_small_roi_below_request() -> None:
    # Richmond Park at S2 10 m ≈ 445 px longest edge: a 1920 request must lock to it.
    s2 = native_max_dim(RICHMOND_PARK, "s2")
    assert 400 <= s2 <= 500
    # Landsat/HLS 30 m is coarser → a smaller native ceiling than S2 for the same ROI.
    assert native_max_dim(RICHMOND_PARK, "landsat") < s2
    # A large ROI's native pixel count exceeds 4K — the min(request, native, 3840)
    # binding cap is then the 4K ceiling, not native.
    assert native_max_dim(PO_VALLEY, "s2") > MAX_DIM_4K


def test_native_pixels_uses_gsd() -> None:
    coarse = native_pixels(RICHMOND_PARK, 60.0)
    fine = native_pixels(RICHMOND_PARK, 10.0)
    assert fine > coarse
    assert coarse >= 2


def test_plan_fps_duration_first_and_frame_first() -> None:
    assert plan_fps(24, fps=10) == 10
    assert plan_fps(24, duration_s=12.0) == 2  # 24 frames / 12 s
    assert plan_fps(120, duration_s=1.0) == 30  # clamped to 30
    assert plan_fps(1, duration_s=100.0) == 1  # clamped to 1
    assert plan_fps(24) == 6  # default


def test_center_crop_to_ratio_aspect_and_even_dims() -> None:
    img = Image.new("RGB", (100, 60), (10, 20, 30))
    square = center_crop_to_ratio(img, 1, 1)
    assert square.size == (60, 60)
    vertical = center_crop_to_ratio(img, 9, 16)
    w, h = vertical.size
    assert w % 2 == 0
    assert h % 2 == 0
    assert abs((w / h) - (9 / 16)) < 0.05


def test_make_card_renders_text_on_dark_field() -> None:
    card = make_card("Richmond Park — one year", (240, 135), subtitle="OpenEarth")
    assert card.size == (240, 135)
    # Not a flat field: the text pixels differ from the background.
    colors = card.getcolors(maxcolors=100000)
    assert colors is not None
    assert len(colors) > 1


def test_watermark_changes_pixels_but_not_size() -> None:
    img = Image.new("RGB", (80, 60), (40, 80, 120))
    marked = watermark_frame(img, "OpenEarth")
    assert marked.size == img.size
    assert list(marked.getdata()) != list(img.getdata())


def test_compose_extra_frames_adds_cards_crop_and_watermark(tmp_path: Path) -> None:
    frames = []
    for i in range(3):
        p = tmp_path / f"frame_{i:04d}.png"
        Image.new("RGB", (80, 60), (i * 40, 60, 90)).save(p)
        frames.append(p)
    work = tmp_path / "work"
    out = compose_extra_frames(
        frames,
        work,
        crop="1:1",
        watermark="OpenEarth",
        title_card="Intro",
        end_card="Fin",
        card_hold=2,
    )
    # 2 title + 3 body + 2 end = 7 frames, all the cropped (square) size.
    assert len(out) == 7
    with Image.open(out[0]) as im:
        assert im.size[0] == im.size[1]  # 1:1 crop
    for p in out:
        assert p.exists()


def test_compose_extra_frames_plain_passthrough(tmp_path: Path) -> None:
    frames = [tmp_path / "frame_0000.png"]
    Image.new("RGB", (40, 40), (1, 2, 3)).save(frames[0])
    out = compose_extra_frames(frames, tmp_path / "w")
    assert len(out) == 1
