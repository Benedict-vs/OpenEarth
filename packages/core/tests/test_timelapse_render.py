"""Stage 2: frame rendering + movie encoding (offline; EE faked, tiny frames)."""

from __future__ import annotations

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
    _frame_dimensions,
    encode_movie,
    expand_frames,
    render_frames,
)

BBOX = BBox(0.0, 0.0, 1.0, 1.0)


def _png_bytes(w: int, h: int, color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
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

    def fake_build(product: str, roi: object, start: date, end: date, source: str) -> object:
        return ("IMG", start)

    def fake_thumb(image: object, spec: object, roi: object, **kw: object) -> str:
        _, start = image  # type: ignore[misc]
        return f"http://fake/{start.isoformat()}"  # type: ignore[union-attr]

    monkeypatch.setattr(tl, "build_mean_composite", fake_build)
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
