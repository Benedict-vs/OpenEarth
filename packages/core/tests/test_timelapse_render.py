"""Stage 2: frame rendering + movie encoding (offline; EE faked, tiny frames)."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

import openearth.timelapse as tl
from openearth.errors import EmptyCollectionError, JobError
from openearth.geometry import BBox
from openearth.timelapse import (
    AnnotationOptions,
    FrameWindow,
    PostOptions,
    _frame_dimensions,
    encode_movie,
    expand_frames,
    render_frames,
)
from openearth.timelapse_post import GradeOptions, NonDisplayFrameError

BBOX = BBox(0.0, 0.0, 1.0, 1.0)


def _png_bytes(w: int, h: int, color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _rgba_png_bytes(w: int, h: int, color: tuple[int, int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _windows(n: int) -> list[FrameWindow]:
    return [
        FrameWindow(i, date(2024, 1, 1 + i), date(2024, 1, 1 + i), f"2024-01-{i + 1:02d}")
        for i in range(n)
    ]


@pytest.fixture
def fake_ee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake the two EE touchpoints render_frames imports by name.

    ``build_mean_composite`` returns a sentinel tagged with the window's start;
    ``thumb_url`` turns it into a URL that encodes that start so the injected
    fetch can return distinct (or failing) bytes per frame.
    """

    def fake_build(
        product: str, roi: object, start: date, end: date, source: str, mode: str = "mean"
    ) -> object:
        return ("IMG", start)

    def fake_thumb(image: object, spec: object, roi: object, **kw: object) -> str:
        _, start = image  # type: ignore[misc]
        return f"http://fake/{start.isoformat()}"  # type: ignore[union-attr]

    monkeypatch.setattr(tl, "build_composite", fake_build)
    monkeypatch.setattr(tl, "thumb_url", fake_thumb)


# ── _frame_dimensions: even rounding ─────────────────────────────


def test_frame_dimensions_rounds_down_to_even_for_video() -> None:
    # Square bbox at the equator → geo_dimensions "101x101".
    assert _frame_dimensions(BBOX, 101, even_dims=True) == (100, 100)
    assert _frame_dimensions(BBOX, 101, even_dims=False) == (101, 101)


def test_frame_dimensions_even_on_non_square_bbox() -> None:
    # 2° wide, 1° tall at the equator → aspect 2 → "101x50" (h=round(50.5)).
    wide = BBox(0.0, 0.0, 2.0, 1.0)
    w, h = _frame_dimensions(wide, 101, even_dims=True)
    assert w % 2 == 0
    assert h % 2 == 0
    assert (w, h) == (100, 50)


# ── render_frames: dense re-indexing + status taxonomy ───────────


def test_render_dense_reindex_skips_empty_and_failed(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows = _windows(5)
    empty_start = windows[1].start.isoformat()
    failed_start = windows[3].start.isoformat()

    def fetch(url: str) -> bytes:
        start = url.rsplit("/", 1)[1]
        if start == failed_start:
            return b"not a png"  # non-PNG → failed
        return _png_bytes(16, 16, (10, 20, 30))

    # window 1 → EmptyCollectionError at mint time.
    real_thumb = tl.thumb_url

    def thumb_or_empty(image: object, spec: object, roi: object, **kw: object) -> str:
        _, start = image  # type: ignore[misc]
        if start.isoformat() == empty_start:  # type: ignore[union-attr]
            raise EmptyCollectionError("collection is empty")
        return real_thumb(image, spec, roi, **kw)

    monkeypatch.setattr(tl, "thumb_url", thumb_or_empty)

    manifest = render_frames(
        "s5p",
        "NO2",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=32,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.0003,
        annotations=AnnotationOptions(),
        fetch=fetch,
    )

    # 3 rendered → dense, hole-free filenames.
    assert manifest.rendered_count == 3
    assert [p.name for p in manifest.frame_paths] == [
        "frame_0000.png",
        "frame_0001.png",
        "frame_0002.png",
    ]
    for p in manifest.frame_paths:
        assert p.exists()
    # No staging files left behind.
    assert list(tmp_path.glob(".staging_*")) == []

    statuses = [r.status for r in manifest.results]
    assert statuses == ["rendered", "empty", "rendered", "failed", "rendered"]


def test_render_writes_manifest_with_dense_indices(fake_ee: None, tmp_path: Path) -> None:
    windows = _windows(3)

    def fetch(url: str) -> bytes:
        return _png_bytes(20, 20, (5, 5, 5))

    render_frames(
        "s5p",
        "NO2",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=24,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.0003,
        annotations=AnnotationOptions(),
        fetch=fetch,
    )

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["dataset"] == "s5p"
    assert manifest["product"] == "NO2"
    assert manifest["width"] % 2 == 0
    assert manifest["height"] % 2 == 0
    assert manifest["vis"] == [0.0, 0.0003]
    assert [f["index"] for f in manifest["frames"]] == [0, 1, 2]
    assert all(f["status"] == "rendered" for f in manifest["frames"])
    assert manifest["frames"][0]["label"] == "2024-01-01"


def test_rendered_frames_match_movie_dimensions(fake_ee: None, tmp_path: Path) -> None:
    windows = _windows(2)

    def fetch(url: str) -> bytes:
        # Deliberately wrong size — render must resize the base to the frame dims.
        return _png_bytes(7, 5, (1, 2, 3))

    manifest = render_frames(
        "s5p",
        "NO2",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=40,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.0003,
        annotations=AnnotationOptions(),
        fetch=fetch,
    )
    with Image.open(manifest.frame_paths[0]) as im:
        assert im.size == (manifest.width, manifest.height)


def test_render_all_empty_raises_job_error(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_empty(image: object, spec: object, roi: object, **kw: object) -> str:
        raise EmptyCollectionError("no images")

    monkeypatch.setattr(tl, "thumb_url", always_empty)

    with pytest.raises(JobError, match="no usable frames"):
        render_frames(
            "s5p",
            "NO2",
            BBOX,
            _windows(3),
            out_dir=tmp_path,
            max_dim=16,
            even_dims=True,
            vis_min=0.0,
            vis_max=0.0003,
            annotations=AnnotationOptions(),
            fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
        )


def test_cancel_after_two_frames_salvages_partial(fake_ee: None, tmp_path: Path) -> None:
    windows = _windows(6)
    seen: list[tuple[int, int]] = []

    manifest = render_frames(
        "s5p",
        "NO2",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=16,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.0003,
        annotations=AnnotationOptions(),
        fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
        on_progress=lambda done, total: seen.append((done, total)),
        should_cancel=lambda: len(seen) >= 2,  # cancel after 2 frames complete
    )
    # Partial render: the two completed frames are kept, flagged cancelled.
    assert manifest.cancelled is True
    assert manifest.rendered_count == 2
    assert [p.name for p in manifest.frame_paths] == ["frame_0000.png", "frame_0001.png"]
    for p in manifest.frame_paths:
        assert p.exists()
    assert list(tmp_path.glob(".staging_*")) == []  # stragglers swept
    # The manifest on disk records the partial state.
    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk["cancelled"] is True


def test_cancel_before_any_frame_raises(fake_ee: None, tmp_path: Path) -> None:
    with pytest.raises(JobError, match="cancelled"):
        render_frames(
            "s5p",
            "NO2",
            BBOX,
            _windows(4),
            out_dir=tmp_path,
            max_dim=16,
            even_dims=True,
            vis_min=0.0,
            vis_max=0.0003,
            annotations=AnnotationOptions(),
            fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
            should_cancel=lambda: True,  # cancelled before the first frame lands
        )
    assert list(tmp_path.glob("frame_*.png")) == []
    assert list(tmp_path.glob(".staging_*")) == []


def test_one_failing_frame_does_not_kill_render(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-empty mint failure is recorded ``failed``, not raised."""
    windows = _windows(4)
    bad_start = windows[2].start.isoformat()
    real_thumb = tl.thumb_url

    def thumb_or_boom(image: object, spec: object, roi: object, **kw: object) -> str:
        _, start = image  # type: ignore[misc]
        if start.isoformat() == bad_start:  # type: ignore[union-attr]
            raise RuntimeError("EE minting blew up on this window")
        return real_thumb(image, spec, roi, **kw)

    monkeypatch.setattr(tl, "thumb_url", thumb_or_boom)

    manifest = render_frames(
        "s5p",
        "NO2",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=16,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.0003,
        annotations=AnnotationOptions(),
        fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
    )
    assert manifest.rendered_count == 3
    assert [r.status for r in manifest.results] == ["rendered", "rendered", "failed", "rendered"]


def test_dead_pipeline_breaker_aborts_after_probe(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consistently failing pipeline aborts at the probe with the breaker
    message (not the terminal all-empty/failed message), and stops consuming
    windows there — on_progress fires exactly EARLY_ABORT_PROBE times."""

    def always_boom(image: object, spec: object, roi: object, **kw: object) -> str:
        raise RuntimeError("EE is down")

    monkeypatch.setattr(tl, "thumb_url", always_boom)
    progress: list[int] = []

    with pytest.raises(JobError, match="failing consistently"):
        render_frames(
            "s5p",
            "NO2",
            BBOX,
            _windows(20),
            out_dir=tmp_path,
            max_dim=16,
            even_dims=True,
            vis_min=0.0,
            vis_max=0.0003,
            annotations=AnnotationOptions(),
            fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
            on_progress=lambda done, total: progress.append(done),
        )
    assert len(progress) == tl.EARLY_ABORT_PROBE  # consumer stopped at the probe


def test_all_empty_windows_do_not_trip_the_breaker(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run of empty windows (winter gaps) is legitimate — the breaker only
    fires on *failures*, so an all-empty render reaches the terminal check."""

    def always_empty(image: object, spec: object, roi: object, **kw: object) -> str:
        raise EmptyCollectionError("no images")

    monkeypatch.setattr(tl, "thumb_url", always_empty)
    progress: list[int] = []

    with pytest.raises(JobError, match="no usable frames"):
        render_frames(
            "s5p",
            "NO2",
            BBOX,
            _windows(20),
            out_dir=tmp_path,
            max_dim=16,
            even_dims=True,
            vis_min=0.0,
            vis_max=0.0003,
            annotations=AnnotationOptions(),
            fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
            on_progress=lambda done, total: progress.append(done),
        )
    assert len(progress) == 20  # every window consumed — no early abort on empties


# ── Phase 10: back-compat (hard rule 2) + manifest v2 honesty surfaces ──


def test_legacy_defaults_render_byte_identical_output(fake_ee: None, tmp_path: Path) -> None:
    """A legacy request (mean, no post, no fallback) must reproduce the exact frame
    bytes captured from the pre-Phase-10 render_frames — pinned golden hashes."""
    windows = _windows(4)

    def fetch(url: str) -> bytes:
        day = int(url.rsplit("-", 1)[1])
        return _png_bytes(40, 30, (day * 20 % 256, 60, 90))

    manifest = render_frames(
        "s2",
        "RGB",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=40,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        fetch=fetch,
    )
    golden = [
        "6ee1e6416410add50ce8c11b",
        "2bbb65f58819561893758978",
        "4b79f1c3044f2bfb5544f1c3",
        "7535b88e8cc13bc69fc1a21c",
    ]
    got = [hashlib.sha256(p.read_bytes()).hexdigest()[:24] for p in manifest.frame_paths]
    assert got == golden
    assert (manifest.width, manifest.height) == (40, 40)


def test_manifest_v2_records_honesty_surfaces_even_for_legacy(
    fake_ee: None, tmp_path: Path
) -> None:
    render_frames(
        "s2",
        "RGB",
        BBOX,
        _windows(3),
        out_dir=tmp_path,
        max_dim=24,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        fetch=lambda url: _png_bytes(24, 24, (10, 20, 30)),
    )
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["composite"] == "mean"
    assert m["post"]["gap_fill"] is False
    assert m["post"]["fallback_source"] is None
    assert m["native_max_dim"] is None  # not supplied → null, v1-compatible
    for f in m["frames"]:
        assert f["source"] == "s2"
        assert f["valid_fraction"] == 1.0  # opaque frames
        assert f["filled_fraction"] == 0.0


def test_manifest_records_native_max_dim_when_supplied(fake_ee: None, tmp_path: Path) -> None:
    """Upscale honesty (decision-9 reversal): the sensor limit rides the manifest."""
    render_frames(
        "s2",
        "RGB",
        BBOX,
        _windows(2),
        out_dir=tmp_path,
        max_dim=24,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        native_max_dim=445,
        fetch=lambda url: _png_bytes(24, 24, (10, 20, 30)),
    )
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["native_max_dim"] == 445


def test_gap_fill_fills_a_transparent_window(fake_ee: None, tmp_path: Path) -> None:
    windows = _windows(3)
    hole_start = windows[1].start.isoformat()

    def fetch(url: str) -> bytes:
        start = url.rsplit("/", 1)[1]
        if start == hole_start:
            return _rgba_png_bytes(16, 16, (0, 0, 0, 0))  # a fully transparent window
        return _rgba_png_bytes(16, 16, (200, 40, 40, 255))

    manifest = render_frames(
        "s2",
        "RGB",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=16,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        post=PostOptions(gap_fill=True),
        fetch=fetch,
    )
    # All three render; the hole window is forward-filled from window 0.
    assert manifest.rendered_count == 3
    fills = [r.filled_fraction for r in manifest.results]
    assert fills[0] == 0.0
    assert fills[1] == 1.0  # the transparent window fully inherited window 0
    assert fills[2] == 0.0
    assert manifest.results[1].valid_fraction == 0.0  # pre-fill honesty surface


def test_gap_fill_blends_the_borrowed_seam_in_the_render_path(
    fake_ee: None, tmp_path: Path
) -> None:
    """Fix D end-to-end: a partial hole is filled AND exposure-matched/feathered."""
    import numpy as np

    windows = _windows(2)
    hole_start = windows[1].start.isoformat()

    def _frame_png(arr: np.ndarray) -> bytes:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        return buf.getvalue()

    full = np.zeros((64, 64, 4), dtype=np.uint8)
    full[..., :3] = 100
    full[..., 3] = 255
    partial = np.zeros((64, 64, 4), dtype=np.uint8)
    partial[:, :32, :3] = 140
    partial[:, :32, 3] = 255  # left half measured brighter; right half a hole

    def fetch(url: str) -> bytes:
        start = url.rsplit("/", 1)[1]
        return _frame_png(partial if start == hole_start else full)

    manifest = render_frames(
        "s2",
        "RGB",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=64,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        post=PostOptions(gap_fill=True),
        fetch=fetch,
    )
    assert manifest.results[1].filled_fraction == pytest.approx(0.5)
    with Image.open(manifest.frame_paths[1]) as im:
        # Deep in the borrowed half: the pasted 100 was gain-matched toward the
        # measured 140 and clamped at +15 % → 115, not a hard 100 paste.
        assert im.getpixel((60, 8))[0] == 115
        # Measured half is untouched.
        assert im.getpixel((8, 8))[0] == 140
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["post"]["seam_blend"] is True


def test_source_ladder_steps_down_to_fallback_on_empty(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows = _windows(3)
    empty_start = windows[1].start

    def build(
        product: str, roi: object, start: date, end: date, source: str, mode: str = "mean"
    ) -> object:
        if source == "s2" and start == empty_start:
            raise EmptyCollectionError("s2 empty here")
        return ("IMG", start, source)

    def thumb(image: object, spec: object, roi: object, **kw: object) -> str:
        _, start, source = image  # type: ignore[misc]
        return f"http://fake/{source}/{start.isoformat()}"  # type: ignore[union-attr]

    monkeypatch.setattr(tl, "build_composite", build)
    monkeypatch.setattr(tl, "thumb_url", thumb)

    manifest = render_frames(
        "s2",
        "RGB",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=16,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        fallback_source="hls",
        fetch=lambda url: _png_bytes(16, 16, (1, 2, 3)),
    )
    sources = [r.source for r in manifest.results]
    assert sources == ["s2", "hls", "s2"]  # window 1 stepped down to HLS


# ── Fix C: sequence exposure + highlight shoulder ────────────────


def test_auto_vis_rgb_hdr_sequence_mints_wide_and_applies_the_shoulder(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snow-bright window widens the minted range; every frame passes through
    ONE fixed shoulder LUT, recorded as manifest ``tone`` (vis stays the truth)."""
    windows = _windows(5)
    snowy = windows[0].start.isoformat()

    def fake_stats(image: object, spec: object, roi: object) -> tuple[float, float]:
        _, start = image  # type: ignore[misc]
        hi = 0.9 if start.isoformat() == snowy else 0.3  # type: ignore[union-attr]
        return (0.0, hi)

    monkeypatch.setattr(tl, "rgb_range_stats", fake_stats)

    manifest = render_frames(
        "s2",
        "RGB",
        BBOX,
        windows,
        out_dir=tmp_path,
        # Large enough that the probe pixel sits above the annotation strip.
        max_dim=200,
        even_dims=True,
        vis_min=None,
        vis_max=None,
        annotations=AnnotationOptions(date_label=False, colorbar=False, scale_bar=False),
        fetch=lambda url: _png_bytes(16, 16, (128, 128, 128)),
    )
    m = json.loads((tmp_path / "manifest.json").read_text())
    # Minted range = envelope with headroom (0 … 0.9 + 5 % span), the honest vis.
    assert m["vis"][0] == 0.0
    assert m["vis"][1] == pytest.approx(0.945)
    assert m["tone"] is not None
    knee_in = m["tone"]["knee_in"]
    assert 0.1 <= knee_in < m["tone"]["knee_out"] < 1.0
    # The frame pixels went through exactly that LUT (same value on every frame).
    from openearth.timelapse_post import highlight_shoulder_lut

    expected = int(highlight_shoulder_lut(knee_in)[128])
    with Image.open(manifest.frame_paths[0]) as im:
        assert im.getpixel((2, 2))[0] == pytest.approx(expected, abs=2)
    with Image.open(manifest.frame_paths[4]) as im:
        assert im.getpixel((2, 2))[0] == pytest.approx(expected, abs=2)


def test_auto_vis_rgb_uniform_sequence_is_linear_no_tone(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tl, "rgb_range_stats", lambda image, spec, roi: (0.01, 0.30))
    render_frames(
        "s2",
        "RGB",
        BBOX,
        _windows(3),
        out_dir=tmp_path,
        max_dim=16,
        even_dims=True,
        vis_min=None,
        vis_max=None,
        annotations=AnnotationOptions(),
        fetch=lambda url: _png_bytes(16, 16, (90, 90, 90)),
    )
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["tone"] is None
    assert m["vis"][1] == pytest.approx(0.30 + 0.29 * 0.05)


def test_explicit_vis_never_samples_exposure(
    fake_ee: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(image: object, spec: object, roi: object) -> tuple[float, float]:
        raise AssertionError("explicit vis must not trigger exposure sampling")

    monkeypatch.setattr(tl, "rgb_range_stats", boom)
    render_frames(
        "s2",
        "RGB",
        BBOX,
        _windows(2),
        out_dir=tmp_path,
        max_dim=16,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        fetch=lambda url: _png_bytes(16, 16, (10, 20, 30)),
    )
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["vis"] == [0.0, 0.3]
    assert m["tone"] is None


def test_render_refuses_post_processing_on_non_rgb_product(fake_ee: None, tmp_path: Path) -> None:
    with pytest.raises(NonDisplayFrameError):
        render_frames(
            "s5p",
            "NO2",
            BBOX,
            _windows(2),
            out_dir=tmp_path,
            max_dim=16,
            even_dims=True,
            vis_min=0.0,
            vis_max=0.0003,
            annotations=AnnotationOptions(),
            post=PostOptions(gap_fill=True),
            fetch=lambda url: _png_bytes(8, 8, (0, 0, 0)),
        )


def test_deflicker_second_pass_finalizes_all_frames(fake_ee: None, tmp_path: Path) -> None:
    windows = _windows(4)

    def fetch(url: str) -> bytes:
        day = int(url.rsplit("-", 1)[1])
        return _png_bytes(20, 20, (day * 40 % 256, 100, 100))

    manifest = render_frames(
        "s2",
        "RGB",
        BBOX,
        windows,
        out_dir=tmp_path,
        max_dim=20,
        even_dims=True,
        vis_min=0.0,
        vis_max=0.3,
        annotations=AnnotationOptions(),
        post=PostOptions(deflicker_strength=1.0, grade=GradeOptions(curve="vivid")),
        fetch=fetch,
    )
    assert manifest.rendered_count == 4
    for p in manifest.frame_paths:
        assert p.exists()
    assert list(tmp_path.glob(".filled_*")) == []  # second-pass staging swept
    assert list(tmp_path.glob(".staging_*")) == []
    m = json.loads((tmp_path / "manifest.json").read_text())
    assert m["post"]["deflicker_strength"] == 1.0
    assert m["post"]["grade"]["curve"] == "vivid"


# ── encode_movie ─────────────────────────────────────────────────


def _make_frames(dir_: Path, n: int, size: tuple[int, int] = (8, 6)) -> list[Path]:
    paths = []
    for i in range(n):
        p = dir_ / f"frame_{i:04d}.png"
        Image.new("RGB", size, (i * 30 % 256, 0, 0)).save(p, format="PNG")
        paths.append(p)
    return paths


def test_encode_gif_roundtrips_through_pillow(tmp_path: Path) -> None:
    frames = _make_frames(tmp_path, 4)
    out = tmp_path / "movie.gif"
    encode_movie(frames, out, fmt="gif", fps=6)
    assert out.exists()
    with Image.open(out) as im:
        assert im.n_frames == 4
    assert list(tmp_path.glob("*.tmp.*")) == []  # atomic — no temp left


def test_encode_mp4(tmp_path: Path) -> None:
    frames = _make_frames(tmp_path, 5)
    out = tmp_path / "movie.mp4"
    encode_movie(frames, out, fmt="mp4", fps=6)
    assert out.exists()
    assert out.stat().st_size > 0
    assert b"ftyp" in out.read_bytes()[:32]  # MP4 container signature


def test_encode_webm(tmp_path: Path) -> None:
    frames = _make_frames(tmp_path, 5)
    out = tmp_path / "movie.webm"
    encode_movie(frames, out, fmt="webm", fps=6)
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"  # EBML (Matroska/WebM) magic


def test_encode_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(JobError, match="no frames"):
        encode_movie([], tmp_path / "x.mp4", fmt="mp4", fps=6)


# ── tween (frame-to-frame smoothing) — plan is pure, tested directly ──


def test_expand_frames_no_tween_is_identity() -> None:
    paths = [Path(f"frame_{i:04d}.png") for i in range(3)]
    plan = expand_frames(paths, tween=0)
    assert plan == [(p, p, 0.0) for p in paths]


def test_expand_frames_inserts_blends_with_correct_alphas() -> None:
    p0, p1, p2 = (Path(f"frame_{i:04d}.png") for i in range(3))
    plan = expand_frames([p0, p1, p2], tween=1)
    # N + (N-1)*tween = 3 + 2 = 5 output frames; blends at α = 1/2.
    assert plan == [
        (p0, p0, 0.0),
        (p0, p1, 0.5),
        (p1, p1, 0.0),
        (p1, p2, 0.5),
        (p2, p2, 0.0),
    ]


def test_expand_frames_tween_three_alphas() -> None:
    p0, p1 = Path("a.png"), Path("b.png")
    plan = expand_frames([p0, p1], tween=3)
    assert plan == [
        (p0, p0, 0.0),
        (p0, p1, 0.25),
        (p0, p1, 0.5),
        (p0, p1, 0.75),
        (p1, p1, 0.0),
    ]


def test_encode_gif_tween_expands_frame_count(tmp_path: Path) -> None:
    frames = _make_frames(tmp_path, 4)
    out = tmp_path / "movie.gif"
    encode_movie(frames, out, fmt="gif", fps=6, tween=1)
    with Image.open(out) as im:
        assert im.n_frames == 7  # 4 + (4-1)*1
