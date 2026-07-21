"""Regression metrics (openearth.methane.metrics) — moved here from
test_calibration_events in Phase 9 Stage 1 when the functions moved to core.

Hand-checked on 4 synthetic points chosen so slope, median ratio, and Theil–Sen
all resolve to exactly 1.0:

    q_ref  = [10, 10, 20, 20]
    q_ours = [11,  9, 24, 16]   (ratios 1.1, 0.9, 1.2, 0.8)
"""

from __future__ import annotations

import numpy as np
import pytest

from openearth.methane.metrics import (
    log_scatter,
    median_ratio,
    slope_through_origin,
    spearman,
    theil_sen_slope,
)

_SYNTH_REF = np.array([10.0, 10.0, 20.0, 20.0])
_SYNTH_OURS = np.array([11.0, 9.0, 24.0, 16.0])


def test_slope_through_origin() -> None:
    # Σ(qo·qr)/Σ(qr²) = (110+90+480+320)/(100+100+400+400) = 1000/1000 = 1.0
    assert slope_through_origin(_SYNTH_OURS, _SYNTH_REF) == pytest.approx(1.0)
    # degenerate: all reference zero → NaN, not a crash
    assert np.isnan(slope_through_origin(_SYNTH_OURS, np.zeros(4)))


def test_median_ratio() -> None:
    # ratios sorted [0.8, 0.9, 1.1, 1.2] → median = (0.9 + 1.1)/2 = 1.0
    assert median_ratio(_SYNTH_OURS, _SYNTH_REF) == pytest.approx(1.0)


def test_theil_sen_slope() -> None:
    # pairwise slopes over dx≠0 pairs: 1.3, 0.5, 1.5, 0.7 → median = (0.7+1.3)/2 = 1.0
    assert theil_sen_slope(_SYNTH_OURS, _SYNTH_REF) == pytest.approx(1.0)


def test_log_scatter_matches_definition() -> None:
    log_ratio = np.log10(_SYNTH_OURS / _SYNTH_REF)
    expected = 1.4826 * np.median(np.abs(log_ratio - np.median(log_ratio)))
    assert log_scatter(_SYNTH_OURS, _SYNTH_REF) == pytest.approx(expected)
    # identical retrievals → zero scatter
    assert log_scatter(_SYNTH_REF, _SYNTH_REF) == pytest.approx(0.0)


def test_spearman_rank_correlation() -> None:
    # Perfectly monotone → ρ = 1; reversed → ρ = −1.
    asc = np.array([10.0, 20.0, 30.0, 40.0])
    assert spearman(np.array([1.0, 2.0, 3.0, 4.0]), asc)[0] == pytest.approx(1.0)
    assert spearman(np.array([4.0, 3.0, 2.0, 1.0]), asc)[0] == pytest.approx(-1.0)
    # n < 3 → NaN (ρ undefined), not a crash.
    rho, pval = spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0]))
    assert np.isnan(rho)
    assert np.isnan(pval)
