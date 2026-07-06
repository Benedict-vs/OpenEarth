"""Stage 7 — S5P Tier-1 screening (offline)."""

from __future__ import annotations

import threading
from datetime import date
from itertools import pairwise

import pytest

from openearth.errors import JobError
from openearth.geometry import BBox
from openearth.methane import tropomi as trop
from openearth.methane.tropomi import (
    _Cell,
    _cells,
    _week_ranges,
    screen_region,
    stitch_hotspots,
)

# ── week chunking ──


def test_week_ranges_cover_span_without_gaps() -> None:
    weeks = _week_ranges(date(2023, 1, 1), date(2023, 2, 1))
    assert weeks[0][0] == date(2023, 1, 1)
    assert weeks[-1][1] == date(2023, 2, 1)
    # Contiguous.
    for (_, prev_end), (next_start, _) in pairwise(weeks):
        assert prev_end == next_start
    # All but the last are 7 days.
    assert all((e - s).days == 7 for s, e in weeks[:-1])


def test_week_ranges_partial_final() -> None:
    weeks = _week_ranges(date(2023, 1, 1), date(2023, 1, 10))
    assert len(weeks) == 2
    assert (weeks[1][1] - weeks[1][0]).days == 2


# ── cell lattice ──


def test_cells_layout_row_major_nw() -> None:
    cells = _cells(BBox(0.0, 0.0, 0.2, 0.2), cell_deg=0.1)
    assert len(cells) == 4
    # idx 0 is the NW cell.
    assert cells[0].lat > cells[2].lat
    assert cells[0].lon < cells[1].lon


def test_cells_refuses_oversized_lattice() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        _cells(BBox(0.0, 0.0, 10.0, 10.0), cell_deg=0.01)  # 1000×1000


# ── stitching / ranking ──


def _feat(idx: int, value: float | None) -> dict[str, object]:
    props: dict[str, object] = {"idx": idx}
    if value is not None:
        props[trop._MEAN_PROP] = value
    return {"properties": props}


def _four_cells() -> list[_Cell]:
    return _cells(BBox(0.0, 0.0, 0.2, 0.2), cell_deg=0.1)


def test_stitch_ranks_persistent_hotspot_first() -> None:
    cells = _four_cells()
    # Cell 0 is persistently high; others are background (~0).
    weekly = [
        [_feat(0, 40.0), _feat(1, 1.0), _feat(2, -1.0), _feat(3, 0.5)],
        [_feat(0, 45.0), _feat(1, -0.5), _feat(2, 0.8), _feat(3, 0.0)],
        [_feat(0, 38.0), _feat(1, 0.3), _feat(2, 0.2), _feat(3, -0.7)],
    ]
    hotspots = stitch_hotspots(weekly, cells, sigma_thresh=2.0, top_n=50)
    assert hotspots[0].lat == cells[0].lat
    assert hotspots[0].lon == cells[0].lon
    assert hotspots[0].weeks_flagged == 3
    assert hotspots[0].weeks_observed == 3
    assert hotspots[0].mean_enh_ppb == pytest.approx(41.0)
    assert hotspots[0].max_enh_ppb == pytest.approx(45.0)
    assert hotspots[0].score > hotspots[1].score


def test_stitch_top_n_limits() -> None:
    cells = _four_cells()
    weekly = [[_feat(i, float(10 - i)) for i in range(4)]]
    assert len(stitch_hotspots(weekly, cells, sigma_thresh=2.0, top_n=2)) == 2


def test_stitch_counts_observed_weeks() -> None:
    cells = _four_cells()
    # Cell 0 observed twice (one week masked → None); cell 1 once.
    weekly = [
        [_feat(0, 30.0), _feat(1, 5.0)],
        [_feat(0, None), _feat(1, None)],
        [_feat(0, 32.0)],
    ]
    hotspots = stitch_hotspots(weekly, cells, sigma_thresh=2.0, top_n=50)
    top = next(h for h in hotspots if h.lat == cells[0].lat and h.lon == cells[0].lon)
    assert top.weeks_observed == 2


def test_stitch_empty_when_no_data() -> None:
    cells = _four_cells()
    assert stitch_hotspots([[_feat(0, None)]], cells, sigma_thresh=2.0, top_n=50) == []


# ── screen_region orchestration (EE faked) ──


def test_screen_region_cancel_between_weeks(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    monkeypatch.setattr(trop, "get_trace_gas_collection", lambda *a, **k: _StubImageColl())
    monkeypatch.setattr(trop, "_cell_features", lambda cells: None)

    def fake_reduce(*_a: object, **_k: object) -> list[dict[str, object]]:
        cancel.set()  # trip cancel after the first week's reduce is requested
        return [_feat(0, 30.0)]

    monkeypatch.setattr(trop, "_reduce_week", fake_reduce)
    with pytest.raises(JobError, match="cancelled"):
        screen_region(
            BBox(0.0, 0.0, 0.2, 0.2),
            date(2023, 1, 1),
            date(2023, 1, 22),
            cell_deg=0.1,
            cancel=cancel,
        )


def test_screen_region_stitches_faked_weeks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trop, "get_trace_gas_collection", lambda *a, **k: _StubImageColl())
    monkeypatch.setattr(trop, "_cell_features", lambda cells: None)
    monkeypatch.setattr(
        trop,
        "_reduce_week",
        lambda bbox, bg, week, fc: [_feat(0, 50.0), _feat(1, 0.0), _feat(2, 0.5), _feat(3, -0.3)],
    )
    hotspots = screen_region(
        BBox(0.0, 0.0, 0.2, 0.2),
        date(2023, 1, 1),
        date(2023, 1, 22),
        cell_deg=0.1,
    )
    assert hotspots
    assert hotspots[0].mean_enh_ppb == pytest.approx(50.0)
    assert all(0.0 <= h.lon <= 0.2 and 0.0 <= h.lat <= 0.2 for h in hotspots)


class _StubImageColl:
    """Stands in for the CH4 collection so .median() is a lazy no-op object."""

    def median(self) -> object:
        return object()

    def mean(self) -> object:
        return object()
