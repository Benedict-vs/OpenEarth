"""Channel-parity law (Phase 9): ``build_channels`` output must never drift.

The ML model's ``ChannelStats`` were frozen from this exact ``build_channels``
output, so a change to what it produces would silently break the train/serve
seam. Stage 2 added opt-in ``mbsp`` kwargs (``robust_cut`` / ``exclude``) with
byte-preserving defaults; ``build_channels`` must keep calling those defaults.
This golden test is the enforcement: if it ever needs regeneration, that is by
definition an ML-retrain phase, not a robustness phase.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from openearth.ee.pixels import GridSpec
from openearth.methane.channels import build_channels
from openearth.methane.retrieval import RetrievalChip
from openearth.methane.scenes import S2Scene

_DATA = Path(__file__).resolve().parent / "data"


def _chip(b11: np.ndarray, b12: np.ndarray) -> RetrievalChip:
    h, w = b11.shape
    grid = GridSpec(x0=0.0, y0=0.0, xscale=1e-4, yscale=1e-4, width=w, height=h)
    scene = S2Scene("t", datetime(2021, 7, 2), 0.0, 22, "Sentinel-2A", 20.0, 5.0)
    return RetrievalChip(scene=scene, grid=grid, bands={"B11": b11, "B12": b12})


def test_build_channels_parity_golden() -> None:
    with np.load(_DATA / "channels_parity_input.npz") as npz:
        target = _chip(npz["target_b11"], npz["target_b12"])
        reference = _chip(npz["ref_b11"], npz["ref_b12"])
    with np.load(_DATA / "channels_parity_golden.npz") as npz:
        golden = npz["channels"]

    produced = build_channels(target, reference)
    assert produced.shape == golden.shape
    assert produced.dtype == golden.dtype
    # Byte-stable: identical NaN pattern and identical finite values.
    assert np.array_equal(np.isnan(produced), np.isnan(golden))
    np.testing.assert_array_equal(produced, golden)
