"""Synthetic chip fixtures for the ML tests — no EE, no network, tiny tensors."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def chips_dir(tmp_path: Path) -> Path:
    """A tiny chips dir: a few (channels, mask) npz over 4 synthetic sites."""
    d = tmp_path / "chips"
    rng = np.random.default_rng(0)
    manifest: dict[str, dict] = {}
    idx = 0
    for site in ("S1", "S2", "S3", "S4"):
        for j in range(4):
            positive = j == 0  # one positive per site
            h, w = 40, 36
            channels = rng.normal(0, 1, (h, w, 5)).astype(np.float32)
            mask = np.zeros((h, w), dtype=bool)
            if positive:
                mask[10:16, 10:16] = True
                channels[10:16, 10:16, 0] = -5.0  # strong −ΔR_MBMP so the baseline fires too
            split = "test" if j == 3 else "train"
            key = f"{split}/{idx}"
            (d / split).mkdir(parents=True, exist_ok=True)
            np.savez_compressed(d / f"{key}.npz", channels=channels, mask=mask)
            manifest[key] = {"status": "ok", "site_id": site, "split": split, "positive": positive}
            idx += 1
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d
