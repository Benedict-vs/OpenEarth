"""Stage 5 — physics-informed ML channel stack (offline, pure NumPy)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from openearth.ee.pixels import GridSpec, grid_for
from openearth.geometry import BBox
from openearth.methane.channels import (
    CHANNELS,
    ChannelStats,
    build_channels,
    candidates_from_prob,
    normalize,
    pad_to_multiple,
    unpad,
)
from openearth.methane.retrieval import RetrievalChip, mbmp, mbsp
from openearth.methane.scenes import S2Scene

_BBOX = BBox(53.0, 38.0, 53.05, 38.05)


def _scene(scene_id: str = "20180101T000000_x") -> S2Scene:
    return S2Scene(scene_id, datetime(2018, 1, 1, tzinfo=UTC), 5.0, 50, "Sentinel-2A", 30.0, 5.0)


def _grid(shape: tuple[int, int]) -> GridSpec:
    g = grid_for(_BBOX, 20)
    return GridSpec(
        x0=g.x0, y0=g.y0, xscale=g.xscale, yscale=g.yscale, width=shape[1], height=shape[0]
    )


def _chip(b11: np.ndarray, b12: np.ndarray, scene_id: str = "s") -> RetrievalChip:
    shape = b11.shape
    zeros = np.zeros(shape, dtype=np.float32)
    bands = {
        "B11": b11.astype(np.float32),
        "B12": b12.astype(np.float32),
        "B4": zeros,
        "B3": zeros,
        "B2": zeros,
    }
    return RetrievalChip(scene=_scene(scene_id), grid=_grid(shape), bands=bands)


def test_channel_order_is_the_input_contract() -> None:
    assert CHANNELS == ("mbmp_delta_r", "mbsp_delta_r", "ratio_b12_b11", "b12", "b11")


def test_build_channels_wires_mbsp_mbmp_and_ratio() -> None:
    rng = np.random.default_rng(0)
    shape = (24, 24)
    r12 = rng.uniform(0.1, 0.3, shape)
    t11 = 1.03 * r12
    t12 = r12 * (1.0 - 0.1 * _blob(shape, 6, 6))  # a plume depresses target B12
    ref12 = rng.uniform(0.1, 0.3, shape)
    ref11 = 1.03 * ref12

    stack = build_channels(_chip(t11, t12), _chip(ref11, ref12, "ref"))
    assert stack.shape == (24, 24, 5)
    assert stack.dtype == np.float32

    t_mbsp, r_mbsp = mbsp(t11, t12), mbsp(ref11, ref12)
    # mbmp is a difference of two near-equal ΔR fields ⇒ near-zero background where
    # there is no plume, so float32 storage needs an absolute tolerance there.
    np.testing.assert_allclose(stack[..., 0], mbmp(t_mbsp, r_mbsp), rtol=1e-4, atol=1e-5)  # mbmp
    np.testing.assert_allclose(stack[..., 1], t_mbsp.delta_r, rtol=1e-4, atol=1e-6)  # mbsp
    np.testing.assert_allclose(stack[..., 2], t12 / t11, rtol=1e-5)  # ratio
    np.testing.assert_allclose(stack[..., 3], t12, rtol=1e-5)  # b12
    np.testing.assert_allclose(stack[..., 4], t11, rtol=1e-5)  # b11


def test_build_channels_nan_safe_on_masked_pixels() -> None:
    shape = (12, 12)
    r12 = np.full(shape, 0.2)
    t11 = 1.02 * r12
    t11[0, 0] = np.nan  # a masked pixel
    stack = build_channels(_chip(t11, r12), _chip(1.02 * r12, r12, "ref"))
    assert np.isnan(stack[0, 0, 4])  # b11 channel carries the NaN through
    # ...but normalize turns every NaN into 0.
    stats = ChannelStats(CHANNELS, (0.0,) * 5, (1.0,) * 5)
    assert np.isfinite(normalize(stack, stats)).all()


def test_build_channels_rejects_mismatched_grids() -> None:
    a = _chip(np.full((8, 8), 0.2), np.full((8, 8), 0.2))
    b = _chip(np.full((10, 10), 0.2), np.full((10, 10), 0.2), "ref")
    with pytest.raises(ValueError, match="grids differ"):
        build_channels(a, b)


def test_channel_stats_rejects_wrong_channel_order() -> None:
    reversed_order = ("b11", "b12", "ratio_b12_b11", "mbsp_delta_r", "mbmp_delta_r")
    with pytest.raises(ValueError, match="CHANNELS"):
        ChannelStats(reversed_order, (0.0,) * 5, (1.0,) * 5)


def test_normalize_is_robust_zscore_with_nan_to_zero() -> None:
    x = np.zeros((2, 2, 5), dtype=np.float32)
    x[..., 0] = 3.0
    x[0, 0, 0] = np.nan
    stats = ChannelStats(CHANNELS, (1.0, 0.0, 0.0, 0.0, 0.0), (2.0, 1.0, 1.0, 1.0, 1.0))
    z = normalize(x, stats)
    # (3 − 1) / (1.4826·2) for the finite pixels of channel 0; NaN pixel → 0.
    assert z[1, 1, 0] == pytest.approx((3.0 - 1.0) / (1.4826 * 2.0), rel=1e-5)
    assert z[0, 0, 0] == 0.0


def test_pad_to_multiple_round_trip() -> None:
    x = np.arange(19 * 13 * 5, dtype=np.float32).reshape(19, 13, 5)
    padded, spec = pad_to_multiple(x, m=32)
    assert padded.shape[0] == 32
    assert padded.shape[1] == 32
    np.testing.assert_array_equal(unpad(padded, spec), x)


def test_pad_to_multiple_noop_when_already_aligned() -> None:
    x = np.zeros((32, 64, 5), dtype=np.float32)
    padded, spec = pad_to_multiple(x, m=32)
    assert padded.shape == (32, 64, 5)
    np.testing.assert_array_equal(unpad(padded, spec), x)


def _blob(shape: tuple[int, int], cr: int, cc: int, rad: int = 3) -> np.ndarray:
    rows, cols = np.indices(shape)
    return (((rows - cr) ** 2 + (cols - cc) ** 2) <= rad**2).astype(float)


def test_candidates_from_prob_filters_scores_and_sorts() -> None:
    prob = np.zeros((40, 40), dtype=np.float32)
    prob += 0.9 * _blob((40, 40), 10, 10, 4)  # big, strong blob
    prob += 0.6 * _blob((40, 40), 30, 30, 1)  # tiny blob (few px)
    cands = candidates_from_prob(prob, threshold=0.5, min_px=10)
    assert len(cands) == 1  # the tiny blob is below min_px
    c = cands[0]
    assert c.n_px >= 10
    assert c.max_prob == pytest.approx(0.9)
    assert c.outline is None  # no grid passed
    assert bool(c.mask[10, 10])


def test_candidates_from_prob_geojson_outline_when_grid_given() -> None:
    prob = 0.8 * _blob((32, 32), 16, 16, 5).astype(np.float32)
    grid = _grid((32, 32))
    cands = candidates_from_prob(prob, threshold=0.5, min_px=5, grid=grid)
    assert len(cands) == 1
    fc = cands[0].outline
    assert fc is not None
    assert fc["type"] == "FeatureCollection"
    assert fc["features"][0]["geometry"]["type"] == "MultiPolygon"


def test_candidates_from_prob_empty_when_nothing_above_threshold() -> None:
    prob = np.full((16, 16), 0.1, dtype=np.float32)
    assert candidates_from_prob(prob, threshold=0.5, min_px=5) == []
