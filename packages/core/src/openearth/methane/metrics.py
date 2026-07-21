"""Shared regression metrics for the methane calibration/benchmark instruments.

These are the aggregate diagnostics that make the calibration harness
(``scripts/calibration_harness.py``) and the S2CH4 truth benchmark
(``scripts/s2ch4_benchmark.py``) falsifiable: a slope / ratio / scatter /
rank-correlation of our retrieved emission against a reference (published rates,
or the dataset's simulated truth). Pure NumPy/scipy, mypy strict — no Earth
Engine, no I/O. They are engineering diagnostics, never gated on an external
anchor.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.stats import spearmanr


def slope_through_origin(q_ours: NDArray[np.float64], q_ref: NDArray[np.float64]) -> float:
    """Least-squares slope of a line through the origin: Σ(qₒ·qᵣ) / Σ(qᵣ²)."""
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_ref = np.asarray(q_ref, dtype=np.float64)
    denom = float(np.sum(q_ref**2))
    if denom == 0.0:
        return float("nan")
    return float(np.sum(q_ours * q_ref) / denom)


def median_ratio(q_ours: NDArray[np.float64], q_ref: NDArray[np.float64]) -> float:
    """median(qₒ / qᵣ) — a robust central bias, insensitive to a few outliers."""
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_ref = np.asarray(q_ref, dtype=np.float64)
    return float(np.median(q_ours / q_ref))


def log_scatter(q_ours: NDArray[np.float64], q_ref: NDArray[np.float64]) -> float:
    """Robust log-scatter s = 1.4826 · MAD(log10(qₒ / qᵣ)) — a spread, not a bias."""
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_ref = np.asarray(q_ref, dtype=np.float64)
    log_ratio = np.log10(q_ours / q_ref)
    mad = float(np.median(np.abs(log_ratio - np.median(log_ratio))))
    return 1.4826 * mad


def theil_sen_slope(q_ours: NDArray[np.float64], q_ref: NDArray[np.float64]) -> float:
    """Theil–Sen pairwise-slope estimator — a robustness cross-check on the LSQ slope.

    Median over all pairs i<j of (qₒⱼ − qₒᵢ)/(qᵣⱼ − qᵣᵢ). Reported, never gated on.
    """
    q_ours = np.asarray(q_ours, dtype=np.float64)
    q_ref = np.asarray(q_ref, dtype=np.float64)
    slopes: list[float] = []
    n = len(q_ref)
    for i in range(n):
        for j in range(i + 1, n):
            dx = q_ref[j] - q_ref[i]
            if dx != 0.0:
                slopes.append(float((q_ours[j] - q_ours[i]) / dx))
    if not slopes:
        return float("nan")
    return float(np.median(slopes))


def spearman(q_ours: NDArray[np.float64], q_ref: NDArray[np.float64]) -> tuple[float, float]:
    """Spearman rank correlation ρ and its p-value — the per-event *skill* metric.

    Reported, never gated on; ``(nan, nan)`` for n < 3 (ρ undefined).
    """
    if len(q_ours) < 3:
        return float("nan"), float("nan")
    result = spearmanr(q_ours, q_ref)
    return float(result.statistic), float(result.pvalue)
