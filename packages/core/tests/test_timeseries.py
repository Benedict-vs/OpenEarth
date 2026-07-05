"""Timeseries v2 engine: pure helpers and the offline-faked engine.

The engine is exercised without a live Earth Engine session by faking
``get_collection`` and ``ee_call`` in the ``openearth.timeseries`` namespace;
the fake collection's ``map`` ignores the (real) server-side reducer closure,
so no EE object is ever constructed.
"""

from __future__ import annotations

import itertools
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

import openearth.timeseries as ts
from openearth.errors import JobError
from openearth.geometry import BBox
from openearth.timeseries import SceneValue, aggregate_daily, chunk_ranges, daily_timeseries

HEIDELBERG = BBox(8.58, 49.35, 8.77, 49.46)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _scene(dt: datetime, value: float | None, count: float) -> SceneValue:
    return SceneValue(timestamp=dt, value=value, count=count)


# ── chunk_ranges (pure) ──────────────────────────────────────


def test_chunk_ranges_exact_multiple() -> None:
    start = date(2020, 1, 1)
    end = start + timedelta(days=180)
    assert chunk_ranges(start, end, 90) == [
        (start, start + timedelta(days=90)),
        (start + timedelta(days=90), end),
    ]


def test_chunk_ranges_partial_final_chunk() -> None:
    start = date(2019, 1, 1)
    end = date(2019, 5, 1)  # 120 days → 90 + 30
    ranges = chunk_ranges(start, end, 90)
    assert ranges == [(date(2019, 1, 1), date(2019, 4, 1)), (date(2019, 4, 1), date(2019, 5, 1))]


def test_chunk_ranges_single_day() -> None:
    day = date(2021, 6, 15)
    assert chunk_ranges(day, day + timedelta(days=1), 90) == [(day, day + timedelta(days=1))]


def test_chunk_ranges_empty_and_reversed() -> None:
    day = date(2021, 6, 15)
    assert chunk_ranges(day, day, 90) == []
    assert chunk_ranges(day, day - timedelta(days=5), 90) == []


def test_chunk_ranges_spans_leap_day_contiguously() -> None:
    # A one-year span across the 2020 leap day, sliced into 90-day chunks.
    start = date(2020, 1, 1)
    end = date(2021, 1, 1)
    ranges = chunk_ranges(start, end, 90)
    # Contiguous, gapless cover of [start, end); every chunk ≤ 90 days.
    assert ranges[0][0] == start
    assert ranges[-1][1] == end
    for (_, prev_end), (next_start, _) in itertools.pairwise(ranges):
        assert prev_end == next_start
    assert all((ce - cs).days <= 90 for cs, ce in ranges)
    # 366 days / 90 → 5 chunks (four full, one 6-day remainder).
    assert len(ranges) == 5


def test_chunk_ranges_rejects_nonpositive_max_days() -> None:
    with pytest.raises(ValueError, match="max_days"):
        chunk_ranges(date(2020, 1, 1), date(2020, 2, 1), 0)


# ── aggregate_daily (pure) ───────────────────────────────────


def test_aggregate_daily_count_weighted_mean() -> None:
    day = datetime(2019, 1, 10, 10, 0, tzinfo=UTC)
    rows = [
        _scene(day, 0.5, 100),
        _scene(day.replace(hour=12), 0.7, 300),  # same UTC date
    ]
    frame = aggregate_daily(rows)
    # (0.5*100 + 0.7*300) / 400 = 0.65; counts sum to 400.
    assert list(frame.index) == [pd.Timestamp("2019-01-10")]
    assert frame.loc["2019-01-10", "value"] == pytest.approx(0.65)
    assert frame.loc["2019-01-10", "count"] == 400


def test_aggregate_daily_drops_none_values_and_sorts() -> None:
    rows = [
        _scene(datetime(2019, 3, 1, tzinfo=UTC), None, 0),  # dropped
        _scene(datetime(2019, 2, 15, tzinfo=UTC), 1.0, 200),
        _scene(datetime(2019, 1, 10, tzinfo=UTC), 0.4, 50),
    ]
    frame = aggregate_daily(rows)
    assert [ts_.strftime("%Y-%m-%d") for ts_ in frame.index] == ["2019-01-10", "2019-02-15"]
    assert frame["value"].tolist() == [0.4, 1.0]
    assert frame["count"].tolist() == [50, 200]


def test_aggregate_daily_empty_is_typed_empty_frame() -> None:
    frame = aggregate_daily([])
    assert frame.empty
    assert list(frame.columns) == ["value", "count"]
    assert frame.index.name == "date"
    assert frame["count"].dtype == "int64"


def test_aggregate_daily_all_none_is_empty() -> None:
    rows = [_scene(datetime(2019, 1, 10, tzinfo=UTC), None, 0)]
    assert aggregate_daily(rows).empty


# ── daily_timeseries engine (offline-faked) ──────────────────


class _FakeFC:
    def __init__(self, features: list[dict[str, Any]]) -> None:
        self._features = features

    def getInfo(self) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": self._features}


class _FakeCollection:
    def __init__(self, features: list[dict[str, Any]]) -> None:
        self._features = features

    def map(self, _fn: Any) -> _FakeFC:  # ignores the (real) reducer closure
        return _FakeFC(self._features)


def _feat(dt: datetime, mean: float | None, count: float) -> dict[str, Any]:
    return {"properties": {"t": _ms(dt), "mean": mean, "count": count}}


@pytest.fixture
def fake_ee(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the EE seam; ``payloads`` maps a chunk-start ISO string to features."""
    state: dict[str, Any] = {"payloads": {}, "ee_calls": 0, "collections": []}

    def fake_get_collection(
        data_key: str, roi: Any, start: str, end: str, source: str
    ) -> _FakeCollection:
        state["collections"].append(start)
        return _FakeCollection(state["payloads"].get(start, []))

    def fake_ee_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        state["ee_calls"] += 1
        return fn(*args, **kwargs)

    monkeypatch.setattr(ts, "get_collection", fake_get_collection)
    monkeypatch.setattr(ts, "ee_call", fake_ee_call)
    return state


def test_engine_end_to_end_two_chunks(fake_ee: dict[str, Any]) -> None:
    fake_ee["payloads"] = {
        "2019-01-01": [
            _feat(datetime(2019, 1, 10, 10, tzinfo=UTC), 0.5, 100),
            _feat(datetime(2019, 1, 10, 12, tzinfo=UTC), 0.7, 300),  # same day → weighted
            _feat(datetime(2019, 2, 15, tzinfo=UTC), 1.0, 200),
            _feat(datetime(2019, 3, 1, tzinfo=UTC), None, 0),  # masked → dropped
        ],
        "2019-04-01": [
            _feat(datetime(2019, 4, 5, tzinfo=UTC), 2.0, 50),
        ],
    }
    progress: list[tuple[int, int]] = []

    def on_chunk(done: int, total: int, chunk_frame: pd.DataFrame) -> None:
        progress.append((done, total))
        assert isinstance(chunk_frame, pd.DataFrame)

    frame = daily_timeseries(
        "NDVI", "s2", HEIDELBERG, date(2019, 1, 1), date(2019, 5, 1), on_chunk=on_chunk
    )

    # Two chunks → two getInfo round-trips and two progress callbacks (1,2).
    assert fake_ee["ee_calls"] == 2
    assert progress == [(1, 2), (2, 2)]
    assert [ts_.strftime("%Y-%m-%d") for ts_ in frame.index] == [
        "2019-01-10",
        "2019-02-15",
        "2019-04-05",
    ]
    assert frame.loc["2019-01-10", "value"] == pytest.approx(0.65)
    assert frame.loc["2019-01-10", "count"] == 400
    assert frame["value"].tolist() == pytest.approx([0.65, 1.0, 2.0])


def test_engine_empty_result_is_not_an_error(fake_ee: dict[str, Any]) -> None:
    # No payloads registered → every chunk returns zero features.
    frame = daily_timeseries("NO2", "s5p", HEIDELBERG, date(2019, 1, 1), date(2019, 3, 1))
    assert frame.empty
    assert list(frame.columns) == ["value", "count"]


def test_engine_refuses_rgb_product(fake_ee: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="RGB"):
        daily_timeseries("RGB", "s2", HEIDELBERG, date(2019, 1, 1), date(2019, 3, 1))
    assert fake_ee["ee_calls"] == 0  # refused before any EE work


def test_engine_cancel_before_dispatch(fake_ee: dict[str, Any]) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(JobError, match="cancelled"):
        daily_timeseries(
            "NDVI", "s2", HEIDELBERG, date(2019, 1, 1), date(2019, 5, 1), cancel=cancel
        )
    assert fake_ee["ee_calls"] == 0  # nothing dispatched


def test_engine_cancel_between_chunks_stops_dispatch(
    fake_ee: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force serial execution so cancellation deterministically halts dispatch.
    monkeypatch.setattr(ts, "get_settings", lambda: _settings(ee_max_concurrency=1))
    fake_ee["payloads"] = {
        "2019-01-01": [_feat(datetime(2019, 1, 10, tzinfo=UTC), 0.5, 100)],
        "2019-04-01": [_feat(datetime(2019, 4, 5, tzinfo=UTC), 2.0, 50)],
    }
    cancel = threading.Event()
    seen: list[int] = []

    def on_chunk(done: int, total: int, chunk_frame: pd.DataFrame) -> None:
        seen.append(done)
        cancel.set()  # cancel arrives while the first chunk is being handled

    with pytest.raises(JobError, match="cancelled"):
        daily_timeseries(
            "NDVI",
            "s2",
            HEIDELBERG,
            date(2019, 1, 1),
            date(2019, 5, 1),
            on_chunk=on_chunk,
            cancel=cancel,
        )
    # Only the first chunk ran; the second was cancelled before dispatch.
    assert seen == [1]
    assert fake_ee["ee_calls"] == 1


def _settings(**overrides: Any) -> Any:
    from openearth.settings import Settings

    return Settings(_env_file=None, **overrides)
