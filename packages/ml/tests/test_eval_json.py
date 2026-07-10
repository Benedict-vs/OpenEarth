"""Guard the committed frozen eval (scripts/data/ml_eval_v1.json).

Skips when the file is absent (before the CV run has produced it); once committed
it must parse, carry the expected schema, have 5 folds, and its aggregate must be
consistent with the per-fold rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

EVAL_JSON = Path(__file__).resolve().parents[3] / "scripts" / "data" / "ml_eval_v1.json"


def _load() -> dict:
    if not EVAL_JSON.exists():
        pytest.skip(f"{EVAL_JSON} not produced yet (run `openearth_ml.train cv`)")
    return json.loads(EVAL_JSON.read_text())


def test_schema_and_provenance_present() -> None:
    doc = _load()
    assert doc["model_version"]
    prov = doc["provenance"]
    for key in ("git_hash", "data_manifest_sha256", "device", "seed", "config", "fold_of_site"):
        assert key in prov, f"missing provenance.{key}"
    assert "gate_model_ge_baseline" in doc


def test_five_folds_and_sites_partitioned() -> None:
    doc = _load()
    assert len(doc["folds"]) == 5
    # every site appears in exactly one fold's val set
    seen: list[str] = []
    for row in doc["folds"]:
        seen.extend(row["val_sites"])
    assert len(seen) == len(set(seen)), "a site was held out in more than one fold"


def test_aggregate_matches_fold_rows() -> None:
    doc = _load()
    folds = doc["folds"]
    agg = doc["aggregate"]
    model_f1 = float(np.mean([r["model"]["f1"] for r in folds]))
    base_f1 = float(np.mean([r["baseline"]["f1"] for r in folds]))
    assert agg["model_scene_f1"] == pytest.approx(model_f1, abs=1e-6)
    assert agg["baseline_scene_f1"] == pytest.approx(base_f1, abs=1e-6)
    assert doc["gate_model_ge_baseline"] == (agg["model_scene_f1"] >= agg["baseline_scene_f1"])
