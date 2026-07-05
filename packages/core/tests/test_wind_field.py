"""Wind field grid math and feature stitching (pure, offline).

The live ``reduceRegions`` path is exercised in ``test_ee_live.py``; here we pin
the pure pieces: the NW-origin, row-major lattice and the masked-cell → NaN
parsing of ``reduceRegions`` output.
"""

from __future__ import annotations

import math

import pytest

from openearth.geometry import BBox
from openearth.methane.wind import _U_BAND, _V_BAND, _field_arrays_from_features, wind_grid

# ── wind_grid (pure) ─────────────────────────────────────────


def test_wind_grid_cell_count_and_indexing() -> None:
    cells = wind_grid(BBox(0, 0, 4, 2), nx=4, ny=2)
    assert len(cells) == 8
    # Row-major from NW: idx == row * nx + col, contiguous from 0.
    assert [c.idx for c in cells] == list(range(8))


def test_wind_grid_nw_origin_and_full_cover() -> None:
    bbox = BBox(-10, 40, 2, 52)  # 12° × 12°
    cells = wind_grid(bbox, nx=3, ny=4)  # dx = 4°, dy = 3°

    nw = cells[0]
    assert nw.idx == 0
    assert (nw.west, nw.north) == pytest.approx((bbox.west, bbox.north))

    # The north row spans the full width along the top edge.
    row0 = cells[:3]
    assert min(c.west for c in row0) == pytest.approx(bbox.west)
    assert max(c.east for c in row0) == pytest.approx(bbox.east)
    assert all(c.north == pytest.approx(bbox.north) for c in row0)

    # The bottom row sits on the south edge; the SE cell closes the box.
    assert all(c.south == pytest.approx(bbox.south) for c in cells[-3:])
    se = cells[-1]
    assert (se.east, se.south) == pytest.approx((bbox.east, bbox.south))


def test_wind_grid_cell_centers() -> None:
    # 2×2 over a 4°×4° box → 2° cells, centers at the quarter points.
    centers = [c.center for c in wind_grid(BBox(0, 0, 4, 4), nx=2, ny=2)]
    assert centers == [
        pytest.approx((1.0, 3.0)),  # NW
        pytest.approx((3.0, 3.0)),  # NE
        pytest.approx((1.0, 1.0)),  # SW
        pytest.approx((3.0, 1.0)),  # SE
    ]


def test_wind_grid_rows_abut_and_descend() -> None:
    cells = wind_grid(BBox(0, 0, 2, 2), nx=2, ny=2)
    assert cells[0].south == pytest.approx(cells[2].north)  # no gap between rows
    assert cells[0].center[1] > cells[2].center[1]  # row 0 is north of row 1


@pytest.mark.parametrize(("nx", "ny"), [(0, 4), (4, 0), (51, 4), (4, 51), (-1, 3)])
def test_wind_grid_rejects_out_of_range(nx: int, ny: int) -> None:
    with pytest.raises(ValueError, match=r"\[1, 50\]"):
        wind_grid(BBox(0, 0, 1, 1), nx, ny)


# ── _field_arrays_from_features (pure) ───────────────────────


def _feature(idx: int, u: float | None, v: float | None) -> dict[str, object]:
    props: dict[str, object] = {"idx": idx}
    if u is not None:
        props[_U_BAND] = u
    if v is not None:
        props[_V_BAND] = v
    return {"properties": props}


def test_field_arrays_place_by_idx_out_of_order() -> None:
    features = [_feature(2, 2.0, -2.0), _feature(0, 0.0, 0.5), _feature(1, 1.0, -1.0)]
    u, v = _field_arrays_from_features(features, 3)
    assert u == (0.0, 1.0, 2.0)
    assert v == (0.5, -1.0, -2.0)


def test_field_arrays_missing_feature_is_nan() -> None:
    u, v = _field_arrays_from_features([_feature(0, 3.0, 4.0)], 3)
    assert u[0] == pytest.approx(3.0)
    assert math.isnan(u[1])
    assert math.isnan(u[2])
    assert math.isnan(v[2])


def test_field_arrays_masked_band_property_is_nan() -> None:
    # A fully-masked cell reduces to no band property at all.
    u, v = _field_arrays_from_features([_feature(0, None, None)], 1)
    assert math.isnan(u[0])
    assert math.isnan(v[0])
