"""Stage 6 — detection orchestrator end-to-end (offline, fully faked EE)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import numpy as np
import pytest

from openearth.ee.pixels import GridSpec, grid_for
from openearth.errors import JobError
from openearth.geometry import BBox
from openearth.methane import detect as detect_mod
from openearth.methane.conversion import forward_signal, load_lut
from openearth.methane.detect import analyze
from openearth.methane.ime import McParams, ime_kg, plume_length_m, u_eff_ms
from openearth.methane.plume import detect_plume
from openearth.methane.retrieval import CHIP_BANDS, RetrievalChip
from openearth.methane.scenes import S2Scene
from openearth.methane.wind import ERA5_LAND_HOURLY_ID, WindSample

_BBOX = BBox(53.95, 38.45, 53.99, 38.49)
_SHAPE = (60, 60)


def _grid() -> GridSpec:
    g = grid_for(_BBOX, 20)
    return GridSpec(
        x0=g.x0, y0=g.y0, xscale=g.xscale, yscale=g.yscale, width=_SHAPE[1], height=_SHAPE[0]
    )


def _target_scene() -> S2Scene:
    return S2Scene(
        "20180619T074619_x",
        datetime(2018, 6, 19, 7, 46, tzinfo=UTC),
        5.0,
        50,
        "Sentinel-2A",
        40.0,
        5.0,
    )


def _reference_scene() -> S2Scene:
    return S2Scene(
        "20180609T074619_x",
        datetime(2018, 6, 9, 7, 46, tzinfo=UTC),
        5.0,
        50,
        "Sentinel-2A",
        40.0,
        5.0,
    )


def _truth_delta_omega() -> np.ndarray:
    rows, cols = np.indices(_SHAPE)
    cr, cc = 30, 30
    return 0.4 * np.exp(-(((rows - cr) ** 2 + (cols - cc) ** 2) / (2 * 6.0**2)))


def _chip_from_delta_omega(scene: S2Scene, d_omega: np.ndarray) -> RetrievalChip:
    """Craft B11/B12 so mbsp+invert reproduces *d_omega* for *scene*.

    With R11 constant and R12 = R11·(1 + m(ΔΩ)), MBSP recovers ΔR ≈ m and the
    LUT inverts it back to ΔΩ.
    """
    lut = load_lut()
    do_grid, m_grid = forward_signal(lut, scene.spacecraft, scene.amf)
    m_field = np.interp(d_omega, do_grid, m_grid)
    r11 = np.full(_SHAPE, 0.2, dtype=np.float32)
    r12 = (r11 * (1.0 + m_field)).astype(np.float32)
    bands = {"B11": r11, "B12": r12}
    for extra in CHIP_BANDS[2:]:
        bands[extra] = np.full(_SHAPE, 0.15, dtype=np.float32)
    return RetrievalChip(scene=scene, grid=_grid(), bands=bands)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_delta: np.ndarray,
    ref_delta: np.ndarray | None = None,
) -> dict[str, int]:
    counts = {"fetch_chip": 0}
    target, reference = _target_scene(), _reference_scene()

    def fake_list_scenes(*_a: object, **_k: object) -> list[S2Scene]:
        return [target, reference]

    def fake_fetch_chip(scene: S2Scene, bbox: BBox, **_k: object) -> RetrievalChip:
        counts["fetch_chip"] += 1
        if scene.scene_id == target.scene_id:
            return _chip_from_delta_omega(scene, target_delta)
        delta = ref_delta if ref_delta is not None else np.zeros(_SHAPE)
        return _chip_from_delta_omega(scene, delta)

    def fake_wind(_roi: object, when: datetime, **_k: object) -> WindSample:
        return WindSample.from_uv(when, 4.0, 0.0, ERA5_LAND_HOURLY_ID)

    monkeypatch.setattr(detect_mod, "list_scenes", fake_list_scenes)
    monkeypatch.setattr(detect_mod, "fetch_chip", fake_fetch_chip)
    monkeypatch.setattr(detect_mod, "sample_wind_at", fake_wind)
    return counts


# ── golden path ──


def test_analyze_recovers_injected_plume_q_within_20pct(monkeypatch: pytest.MonkeyPatch) -> None:
    truth = _truth_delta_omega()
    _install_fakes(monkeypatch, target_delta=truth)

    result = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=300, seed=3))
    grid = _grid()
    pm = detect_plume(truth, grid, k_sigma=2.0)
    q_true = u_eff_ms(4.0) / plume_length_m(pm.mask, grid) * ime_kg(truth, pm.mask, grid) * 3600.0

    assert "no_plume" not in result.flags
    assert result.emission.q_kg_h == pytest.approx(q_true, rel=0.20)
    assert result.reference is not None
    assert result.method == "mbmp"
    assert np.isfinite(result.calibration["c_target"])
    assert np.isfinite(result.calibration["c_ref"])


def test_progress_called_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(monkeypatch, target_delta=_truth_delta_omega())
    steps: list[tuple[int, int, str]] = []
    analyze(
        _BBOX,
        "20180619T074619_x",
        mc=McParams(n=50),
        on_progress=lambda i, n, label: steps.append((i, n, label)),
    )
    assert [s[0] for s in steps] == [1, 2, 3, 4, 5, 6, 7]
    assert all(s[1] == 7 for s in steps)


def test_cancel_between_steps_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(monkeypatch, target_delta=_truth_delta_omega())
    cancel = threading.Event()

    def on_progress(i: int, _n: int, _label: str) -> None:
        if i == 3:
            cancel.set()  # trip the cancel before step 4's check

    with pytest.raises(JobError, match="cancelled"):
        analyze(
            _BBOX, "20180619T074619_x", mc=McParams(n=50), on_progress=on_progress, cancel=cancel
        )


def test_no_plume_is_valid_result(monkeypatch: pytest.MonkeyPatch) -> None:
    # Flat ΔΩ ⇒ no plume; a valid result with NaN emission, not an exception.
    _install_fakes(monkeypatch, target_delta=np.zeros(_SHAPE))
    result = analyze(_BBOX, "20180619T074619_x", mc=McParams(n=50))
    assert "no_plume" in result.flags
    assert np.isnan(result.emission.q_kg_h)
    assert result.plume.n_pixels == 0


def test_mbsp_skips_reference_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    counts = _install_fakes(monkeypatch, target_delta=_truth_delta_omega())
    steps: list[tuple[int, int, str]] = []
    result = analyze(
        _BBOX,
        "20180619T074619_x",
        method="mbsp",
        mc=McParams(n=50),
        on_progress=lambda i, n, label: steps.append((i, n, label)),
    )
    assert counts["fetch_chip"] == 1  # target only
    assert result.reference is None
    assert steps[1] == (2, 7, "skipped")
    assert [s[0] for s in steps] == [1, 2, 3, 4, 5, 6, 7]
