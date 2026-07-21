"""Stage 1 — S2CH4 benchmark scoring (offline, zero Earth Engine, zero network).

Two layers: pure aggregate-math on synthetic ProductScores, and a fixture-driven
end-to-end smoke on the three committed Hassi plume0 files.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = Path(__file__).resolve().parent / "data" / "s2ch4"
_PREFIX = "S2A_MSICH4_20210702T101031_N0301_R022_T32SKA_20210702T121947_plume0"


def _load_benchmark():  # type: ignore[no-untyped-def]
    path = _REPO_ROOT / "scripts" / "s2ch4_benchmark.py"
    spec = importlib.util.spec_from_file_location("s2ch4_benchmark", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # let frozen dataclasses resolve annotations (3.14)
    spec.loader.exec_module(module)
    return module


def _score(bm, **overrides):  # type: ignore[no-untyped-def]
    base = dict(
        site="hassi",
        plume=0,
        q_true_kg_h=5000.0,
        method="mbmp",
        source_mode="hinted",
        detected=True,
        n_px=20,
        iou=0.5,
        q_est_kg_h=5000.0,
        xch4_bias_ppb=0.0,
        xch4_rms_ppb=0.0,
        invalid_fraction=0.0,
        u10_ms=2.7,
        ime_kg=1.0,
        l_m=100.0,
    )
    base.update(overrides)
    return bm.ProductScore(**base)


# ── aggregate math ──


def test_q_bin_index() -> None:
    bm = _load_benchmark()
    assert bm._q_bin_index(500) == 0
    assert bm._q_bin_index(1500) == 1
    assert bm._q_bin_index(50000) == len(bm._Q_BIN_EDGES) - 2
    assert bm._q_bin_index(100) is None  # below the lowest edge
    assert bm._q_bin_index(50001) is None  # at/above the top edge (half-open)


def test_iou() -> None:
    bm = _load_benchmark()
    a = np.array([[True, True], [False, False]])
    b = np.array([[True, False], [False, False]])
    assert bm._iou(a, b) == pytest.approx(0.5)  # 1 shared / 2 union
    empty = np.zeros((2, 2), dtype=bool)
    assert bm._iou(empty, empty) == 0.0


def test_truth_mask_is_q_invariant() -> None:
    bm = _load_benchmark()
    field = np.array([[0.0, 1.0], [0.02, 0.5]])
    mask = bm._truth_mask(field)
    # 5 % of peak (1.0) = 0.05 → the 0.02 pixel drops out.
    assert mask.tolist() == [[False, True], [False, True]]
    # Linear in flux → the same footprint at 10× (the plan's non-degeneracy claim).
    assert np.array_equal(bm._truth_mask(field * 10.0), mask)
    assert not bm._truth_mask(np.zeros((2, 2))).any()  # plume-free → empty


def test_min_detectable_q() -> None:
    bm = _load_benchmark()
    curve = [
        {"q_lo_kg_h": 500, "q_hi_kg_h": 1000, "n": 5, "detect_rate": 0.2},
        {"q_lo_kg_h": 1000, "q_hi_kg_h": 2000, "n": 5, "detect_rate": 0.6},
        {"q_lo_kg_h": 2000, "q_hi_kg_h": 3000, "n": 5, "detect_rate": 1.0},
    ]
    assert bm._min_detectable_q(curve) == 1000
    none_curve = [{"q_lo_kg_h": 500, "q_hi_kg_h": 1000, "n": 5, "detect_rate": 0.2}]
    assert bm._min_detectable_q(none_curve) is None


def test_detection_curve() -> None:
    bm = _load_benchmark()
    rows = [
        _score(bm, q_true_kg_h=600.0, detected=True),
        _score(bm, q_true_kg_h=700.0, detected=False),
        _score(bm, q_true_kg_h=5000.0, detected=True),
    ]
    curve = bm._detection_curve(rows)
    bin0 = next(b for b in curve if b["q_lo_kg_h"] == 500)
    assert bin0["n"] == 2
    assert bin0["detect_rate"] == pytest.approx(0.5)


def test_q_recovery_perfect() -> None:
    bm = _load_benchmark()
    rows = [_score(bm, q_true_kg_h=q, q_est_kg_h=q) for q in (1000.0, 5000.0, 20000.0)]
    rec = bm._q_recovery(rows)
    assert rec["slope_through_origin"] == pytest.approx(1.0)
    assert rec["median_ratio"] == pytest.approx(1.0)
    assert rec["log_scatter"] == pytest.approx(0.0)


def test_q_recovery_excludes_invalid_and_undetected() -> None:
    bm = _load_benchmark()
    rows = [
        _score(bm, detected=False),
        _score(bm, invalid_fraction=0.9),
        _score(bm, q_est_kg_h=float("nan")),
    ]
    assert bm._q_recovery(rows)["n"] == 0


def test_alpha_beta_insufficient_wind_diversity() -> None:
    bm = _load_benchmark()
    # All points at one U10 → span 0 → insufficient regardless of the fit.
    rows = [
        _score(bm, method="mbmp", source_mode="hinted", u10_ms=2.7, q_true_kg_h=q, ime_kg=1.0)
        for q in (1000.0, 5000.0, 20000.0)
    ]
    ab = bm._alpha_beta(rows)
    assert ab["decision"] == "insufficient_wind_diversity"
    assert ab["adopt_refit"] is False


# ── fixture-driven end-to-end smoke ──


def _fixture(q: str) -> Path:
    return _FIXTURES / f"{_PREFIX}_{q}"


def test_smoke_q50000_detects_both_modes() -> None:
    bm = _load_benchmark()
    scores = bm.score_all(sorted(_FIXTURES.glob(f"{_PREFIX}_*")))
    q50 = [s for s in scores if s.q_true_kg_h == 50000.0]
    assert q50, "no Q50000 scores produced"
    for mode in ("hinted", "blind"):
        for method in ("mbsp", "mbmp"):
            row = next(s for s in q50 if s.method == method and s.source_mode == mode)
            assert row.detected, f"{method}/{mode} failed to detect Q50000"


def test_smoke_q50000_hinted_has_positive_iou() -> None:
    bm = _load_benchmark()
    scores = bm.score_all(sorted(_FIXTURES.glob(f"{_PREFIX}_*")))
    row = next(
        s
        for s in scores
        if s.q_true_kg_h == 50000.0 and s.method == "mbmp" and s.source_mode == "hinted"
    )
    assert row.iou > 0.0


def test_smoke_q0_self_reference_is_no_plume() -> None:
    bm = _load_benchmark()
    # MBMP of the plume-free Q0 against itself → an all-zero field → no plume.
    ref = bm.read_product(_fixture("Q0"))
    ref_dr = bm.mbsp(ref.bands["B11"], ref.bands["B12"]).delta_r
    passed = bm._invert_pass(ref_dr, bm.SPACECRAFT, ref.amf)
    from openearth.methane.plume import detect_plume

    plume = detect_plume(passed.mask - passed.mask, ref.grid)
    assert plume.n_pixels == 0
