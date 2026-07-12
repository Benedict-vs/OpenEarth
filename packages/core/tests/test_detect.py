"""Stage 6 — detection orchestrator end-to-end (offline, fully faked EE)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

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
    target: S2Scene | None = None,
    reference: S2Scene | None = None,
) -> dict[str, int]:
    counts = {"fetch_chip": 0}
    target = target or _target_scene()
    reference = reference or _reference_scene()

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


_V2_SNAPSHOT = Path(__file__).parent / "data" / "ch4_lut_v2_snapshot.npz"


def test_footprint_invariant_under_lut_swap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stage 2: the plume mask is thresholded on the ΔΩ from the FROZEN mask LUT, so swapping the
    # *reporting* LUT must leave the mask BIT-IDENTICAL while the reported ΔΩ (and therefore
    # IME/Q) change — the footprint is invariant to a reporting-LUT recalibration by construction.
    truth = _truth_delta_omega()  # crafts R11/R12 via the packaged v3 LUT → ΔR is fixed
    _install_fakes(monkeypatch, target_delta=truth, ref_delta=np.zeros(_SHAPE))
    v3 = load_lut()
    v2 = load_lut(_V2_SNAPSHOT)
    assert v3.version != v2.version

    # Only the reporting `load_lut` is swapped; `load_mask_lut` (the frozen mask inversion) is not.
    monkeypatch.setattr(detect_mod, "load_lut", lambda *_a, **_k: v3)
    r3 = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=80, seed=1))
    monkeypatch.setattr(detect_mod, "load_lut", lambda *_a, **_k: v2)
    r2 = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=80, seed=1))

    # The point of the stage: identical footprint under any reporting-LUT substitution.
    assert r3.plume.n_pixels > 0
    assert np.array_equal(r3.plume.mask, r2.plume.mask)
    # And the swap genuinely changed the reported columns (else the test proves nothing).
    assert not np.allclose(r3.delta_omega, r2.delta_omega, equal_nan=True)
    assert r3.emission.ime_kg != r2.emission.ime_kg


def test_two_sigmas_are_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    # After Stage 2 there are two σ's in different units: the mask threshold σ (ΔR space,
    # on PlumeMask) and the retrieval-noise σ (ΔΩ, on the estimate). They must not coincide.
    _install_fakes(monkeypatch, target_delta=_truth_delta_omega())
    result = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=80, seed=1))
    assert np.isfinite(result.plume.sigma)  # ΔR-space mask σ
    assert np.isfinite(result.emission.sigma_noise_delta_omega)  # ΔΩ-space noise σ
    assert result.plume.sigma != result.emission.sigma_noise_delta_omega


# ── Stage 2 diagnostics (fixes 2, 3, 4) ──


def test_clip_fractions_present_and_clipped_inversion_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The information-free clipped_inversion flag is replaced by per-pass in-mask
    edge fractions (fix 3)."""
    _install_fakes(monkeypatch, target_delta=_truth_delta_omega(), ref_delta=np.zeros(_SHAPE))
    result = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=60, seed=1))
    assert "clipped_inversion" not in result.flags  # dead flag
    assert set(result.clip_fractions) == {"target_lo", "target_hi", "ref_lo", "ref_hi"}
    assert all(0.0 <= v <= 1.0 for v in result.clip_fractions.values())


def test_mask_stability_diagnostic_populated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MC k-sweep pixel counts are surfaced for the mask-stability flag (fix 4c)."""
    _install_fakes(monkeypatch, target_delta=_truth_delta_omega())
    result = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=60, seed=1))
    by_k = result.emission.mask_npx_by_k
    assert by_k
    assert len(by_k) == len(McParams().k_grid)
    assert all(isinstance(v, int) for v in by_k.values())


def test_reference_contamination_flagged_only_when_reference_has_plume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reference scene that itself shows an enhancement near the source is flagged
    (fix 2-flag); a clean reference is not."""
    blob = _truth_delta_omega()  # same Gaussian centred at (30, 30)
    _install_fakes(monkeypatch, target_delta=_truth_delta_omega(), ref_delta=blob)
    contaminated = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=50, seed=1))
    assert "possible_reference_contamination" in contaminated.flags

    _install_fakes(monkeypatch, target_delta=_truth_delta_omega(), ref_delta=np.zeros(_SHAPE))
    clean = analyze(_BBOX, "20180619T074619_x", method="mbmp", mc=McParams(n=50, seed=1))
    assert "possible_reference_contamination" not in clean.flags


def test_mgrs_tile_parse() -> None:
    assert detect_mod._mgrs_tile("20180619T074619_20180619T075534_T39RUN") == "39RUN"
    assert detect_mod._mgrs_tile("20180619T074619_x") is None


def test_cross_tile_reference_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reference from a different UTM tile is flagged (fix 4b / Tier 1 F5)."""
    target = S2Scene(
        "20180619T074619_20180619T075534_T39RUN",
        datetime(2018, 6, 19, 7, 46, tzinfo=UTC),
        5.0,
        50,
        "Sentinel-2A",
        40.0,
        5.0,
    )
    reference = S2Scene(
        "20180609T074619_20180609T075534_T40RUN",  # different MGRS tile
        datetime(2018, 6, 9, 7, 46, tzinfo=UTC),
        5.0,
        50,
        "Sentinel-2A",
        40.0,
        5.0,
    )
    _install_fakes(
        monkeypatch,
        target_delta=_truth_delta_omega(),
        ref_delta=np.zeros(_SHAPE),
        target=target,
        reference=reference,
    )
    result = analyze(_BBOX, target.scene_id, method="mbmp", mc=McParams(n=50, seed=1))
    assert "cross_tile_reference" in result.flags


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


# ── Phase 8: composite reference (opt-in) ──


def _member(scene_id: str, day: int, *, sun_zenith: float = 40.0) -> S2Scene:
    """A same-orbit, same-spacecraft reference-set candidate."""
    return S2Scene(
        scene_id,
        datetime(2018, 6, day, 7, 46, tzinfo=UTC),
        5.0,
        50,
        "Sentinel-2A",
        sun_zenith,
        5.0,
    )


def _install_composite_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_delta: np.ndarray,
    members: list[S2Scene],
    member_delta: np.ndarray | None = None,
) -> dict[str, int]:
    target = _target_scene()
    counts = {"fetch_chip": 0}

    def fake_list_scenes(*_a: object, **_k: object) -> list[S2Scene]:
        return [target, *members]

    def fake_fetch_chip(scene: S2Scene, bbox: BBox, **_k: object) -> RetrievalChip:
        counts["fetch_chip"] += 1
        if scene.scene_id == target.scene_id:
            return _chip_from_delta_omega(scene, target_delta)
        delta = member_delta if member_delta is not None else np.zeros(_SHAPE)
        return _chip_from_delta_omega(scene, delta)

    def fake_wind(_roi: object, when: datetime, **_k: object) -> WindSample:
        return WindSample.from_uv(when, 4.0, 0.0, ERA5_LAND_HOURLY_ID)

    monkeypatch.setattr(detect_mod, "list_scenes", fake_list_scenes)
    monkeypatch.setattr(detect_mod, "fetch_chip", fake_fetch_chip)
    monkeypatch.setattr(detect_mod, "sample_wind_at", fake_wind)
    return counts


def test_composite_reference_medians_members(monkeypatch: pytest.MonkeyPatch) -> None:
    members = [_member(f"20180{d:02d}09T074619_x", d) for d in (6, 4, 2)] + [
        _member("20180531T074619_x", 1),
        _member("20180528T074619_x", 1),
    ]
    counts = _install_composite_fakes(
        monkeypatch, target_delta=_truth_delta_omega(), members=members
    )
    result = analyze(
        _BBOX,
        "20180619T074619_x",
        method="mbmp",
        reference_mode="composite",
        mc=McParams(n=60, seed=1),
    )
    assert result.reference_mode == "composite"
    assert len(result.reference_members) == detect_mod.COMPOSITE_SIZE  # 5 medianed
    assert counts["fetch_chip"] == 1 + detect_mod.COMPOSITE_SIZE  # target + k members
    assert "composite_reference_unavailable" not in result.flags
    # The composite reference is the nearest member (display anchor).
    assert result.reference is not None
    assert result.reference.scene_id == result.reference_members[0].scene_id


def test_composite_falls_back_to_single_when_too_few(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only 2 eligible members (< COMPOSITE_MIN) → graceful single fallback.
    members = [_member("20180609T074619_x", 9), _member("20180604T074619_x", 4)]
    counts = _install_composite_fakes(
        monkeypatch, target_delta=_truth_delta_omega(), members=members
    )
    result = analyze(
        _BBOX,
        "20180619T074619_x",
        method="mbmp",
        reference_mode="composite",
        mc=McParams(n=50, seed=1),
    )
    assert "composite_reference_unavailable" in result.flags
    assert result.reference_mode == "single"
    assert result.reference_members == []
    assert counts["fetch_chip"] == 2  # target + one single reference


def test_composite_amf_spread_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Members whose solar zenith spans a wide range → AMF max−min > AMF_SPREAD_MAX.
    members = [
        _member("20180609T074619_x", 9, sun_zenith=30.0),
        _member("20180604T074619_x", 4, sun_zenith=40.0),
        _member("20180530T074619_x", 1, sun_zenith=55.0),
    ]
    _install_composite_fakes(monkeypatch, target_delta=_truth_delta_omega(), members=members)
    result = analyze(
        _BBOX,
        "20180619T074619_x",
        method="mbmp",
        reference_mode="composite",
        mc=McParams(n=50, seed=1),
    )
    assert "composite_amf_spread" in result.flags
    assert result.composite_amf_spread > detect_mod.AMF_SPREAD_MAX


def test_explicit_reference_scene_id_ignores_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    members = [_member(f"2018060{d}T074619_x", d) for d in (9, 4, 2)]
    _install_composite_fakes(monkeypatch, target_delta=_truth_delta_omega(), members=members)
    result = analyze(
        _BBOX,
        "20180619T074619_x",
        method="mbmp",
        reference_scene_id="20180609T074619_x",  # explicit ⇒ single, composite ignored
        reference_mode="composite",
        mc=McParams(n=50, seed=1),
    )
    assert result.reference_mode == "single"
    assert result.reference_members == []
    assert result.reference is not None
    assert result.reference.scene_id == "20180609T074619_x"


def test_median_composite_chip_nan_behaviour() -> None:
    from openearth.methane.detect import _median_composite_chip

    grid = _grid()

    def chip(val: float, nan_at: tuple[int, int] | None = None) -> RetrievalChip:
        bands = {b: np.full(_SHAPE, val, dtype=np.float32) for b in CHIP_BANDS}
        if nan_at is not None:
            for b in CHIP_BANDS:
                bands[b][nan_at] = np.nan
        return RetrievalChip(scene=_member("m", 9), grid=grid, bands=bands)

    # Three members; one pixel NaN in a single member medians over the other two.
    composite = _median_composite_chip([chip(1.0, nan_at=(0, 0)), chip(2.0), chip(3.0)])
    assert composite.bands["B11"][0, 0] == pytest.approx(2.5)  # median(2, 3)
    assert composite.bands["B11"][5, 5] == pytest.approx(2.0)  # median(1, 2, 3)
    # A pixel NaN in every member stays NaN.
    all_nan = _median_composite_chip([chip(1.0, (1, 1)), chip(2.0, (1, 1)), chip(3.0, (1, 1))])
    assert np.isnan(all_nan.bands["B11"][1, 1])
