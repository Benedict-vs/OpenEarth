"""Stage 0 — S2CH4 benchmark reader (offline, zero Earth Engine, zero network).

Exercises ``scripts/s2ch4_benchmark.py``'s reader half against the three
committed Hassi plume0 fixtures (Q0, Q5000, Q50000). The band-order and
truth-linearity checks below are the empirical pins from the planning session,
now regression tests: they fail loudly if the L1C band indices are ever
mis-mapped or the truth field is misread.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

import h5py
import numpy as np
import pytest

from openearth.methane.plume import pixel_area_m2
from openearth.methane.scenes import S2Scene

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = Path(__file__).resolve().parent / "data" / "s2ch4"
_PREFIX = "S2A_MSICH4_20210702T101031_N0301_R022_T32SKA_20210702T121947_plume0"


def _load_benchmark():  # type: ignore[no-untyped-def]
    path = _REPO_ROOT / "scripts" / "s2ch4_benchmark.py"
    spec = importlib.util.spec_from_file_location("s2ch4_benchmark", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's frozen dataclasses can resolve their
    # (PEP 563 string) annotations via sys.modules under Python 3.14.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(q: str) -> Path:
    return _FIXTURES / f"{_PREFIX}_{q}"


# ── filename parsing ──


def test_parse_product_name() -> None:
    bm = _load_benchmark()
    name = bm.parse_product_name(f"{_PREFIX}_Q50000")
    assert name.spacecraft == "Sentinel-2A"
    assert name.site == "hassi"
    assert name.tile == "32SKA"
    assert name.acquired == date(2021, 7, 2)
    assert name.plume == 0
    assert name.q_true_kg_h == 50000.0


def test_parse_product_name_q0_is_plume_free() -> None:
    bm = _load_benchmark()
    assert bm.parse_product_name(f"{_PREFIX}_Q0").q_true_kg_h == 0.0


def test_parse_product_name_rejects_non_product() -> None:
    bm = _load_benchmark()
    with pytest.raises(ValueError, match="not an S2CH4 product"):
        bm.parse_product_name("README.md")


# ── grid from lat/lon ──


def test_grid_shape_and_scale() -> None:
    bm = _load_benchmark()
    product = bm.read_product(_fixture("Q0"))
    grid = product.grid
    assert (grid.width, grid.height) == (75, 75)
    assert grid.xscale > 0
    assert grid.yscale > 0
    # The simulation is on 20 m S2 SWIR pixels → ~400 m² per pixel (±5 %).
    assert pixel_area_m2(grid) == pytest.approx(400.0, rel=0.05)


# ── band-order pin (the empirical planning check, now a regression test) ──


def test_band_order_pin() -> None:
    bm = _load_benchmark()
    q0 = bm.read_product(_fixture("Q0"))
    q50 = bm.read_product(_fixture("Q50000"))

    d12 = np.max(np.abs(q50.bands["B12"] - q0.bands["B12"]))
    d11 = np.max(np.abs(q50.bands["B11"] - q0.bands["B11"]))
    # B12 (idx 12) carries the strong CH4 absorption; B11 (idx 11) a weaker dimming.
    assert d12 > d11 > 0.0
    assert d12 == pytest.approx(0.202, abs=0.01)

    # The context bands are static surface (identical across flux) — a mis-mapped
    # index would leak plume dimming into one of them.
    for band in ("B8A", "B4", "B3", "B2"):
        assert np.array_equal(q50.bands[band], q0.bands[band])


# ── truth-field linearity pin ──


def test_truth_xch4_linearity() -> None:
    bm = _load_benchmark()
    q5 = bm.read_product(_fixture("Q5000"))
    q50 = bm.read_product(_fixture("Q50000"))
    ratio = float(np.max(q50.truth_xch4)) / float(np.max(q5.truth_xch4))
    # 10× the flux → ~10× the peak truth enhancement (forward model is linear).
    assert ratio == pytest.approx(10.0, rel=0.05)
    # Q0 has no enhancement; Q50000 peaks near the planning-verified 4.23e-5.
    assert float(np.max(bm.read_product(_fixture("Q0")).truth_xch4)) == pytest.approx(0.0, abs=1e-9)
    assert float(np.max(q50.truth_xch4)) == pytest.approx(4.23e-5, rel=0.05)


# ── AMF formula equality with S2Scene.amf ──


def test_amf_matches_s2scene_formula() -> None:
    bm = _load_benchmark()
    product = bm.read_product(_fixture("Q0"))
    with h5py.File(_fixture("Q0"), "r") as f:
        sza, vza = float(f["SZA"][()]), float(f["VZA"][()])
    scene = S2Scene(
        scene_id="fixture",
        time=datetime(2021, 7, 2),
        cloud_pct=0.0,
        relative_orbit=22,
        spacecraft="Sentinel-2A",
        sun_zenith_deg=sza,
        view_zenith_deg=vza,
    )
    assert product.amf == pytest.approx(scene.amf)
    assert product.u10_ms == pytest.approx(2.693, abs=0.01)
