"""Label-quality gate tests (fix 7 / Tier 2 F3) — synthetic chips, packaged LUT."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from openearth_ml.data import ChipRef
from openearth_ml.labelq import label_integral_delta_omega, quality_filter


def _make_chip(path: Path, delta_r_in_mask: float, *, positive: bool = True) -> ChipRef:
    channels = np.zeros((30, 30, 5), dtype=np.float32)
    mask = np.zeros((30, 30), dtype=bool)
    if positive:
        mask[10:16, 10:16] = True
        channels[10:16, 10:16, 0] = delta_r_in_mask  # MBMP ΔR
    np.savez_compressed(path, channels=channels, mask=mask)
    return ChipRef(path, "S1", "train", positive)


def test_label_gate_partitions_by_delta_omega_sign(tmp_path: Path) -> None:
    # ΔR < 0 → ΔΩ > 0 (real methane signal); ΔR > 0 clips to the LUT's negative ΔΩ edge.
    methane = _make_chip(tmp_path / "pos_ok.npz", -0.02)
    contradictory = _make_chip(tmp_path / "pos_bad.npz", 0.02)
    negative = _make_chip(tmp_path / "neg.npz", 0.0, positive=False)

    assert label_integral_delta_omega(methane) > 0.0
    assert label_integral_delta_omega(contradictory) <= 0.0

    lq = quality_filter([methane, contradictory, negative])
    assert lq.n_positive == 2
    assert lq.n_excluded == 1
    assert contradictory in lq.excluded
    assert methane in lq.kept
    assert negative in lq.kept  # negatives are always kept
