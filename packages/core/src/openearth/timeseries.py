"""Timeseries v2 engine: chunked, concurrent, progress-reporting.

Guiding split (plan.md): Earth Engine reduces, NumPy/pandas does the math.
The engine slices a date range into fixed chunks, reduces each chunk with a
single ``getInfo`` round-trip (one server-side ``reduceRegion`` mapped over
the collection), and aggregates the per-scene means into a daily series with
plain pandas — so everything downstream of Earth Engine is offline-testable.

The two pure helpers (:func:`chunk_ranges`, :func:`aggregate_daily`) carry
the logic; :func:`daily_timeseries` is the only Earth-Engine-touching path
and it constructs no EE objects except inside the server-side map function,
which keeps it monkeypatchable without a live session.
"""

from __future__ import annotations

import itertools
import threading
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import ee
import numpy as np
import pandas as pd

from openearth.catalog import get_dataset, resolve_product
from openearth.ee.client import ee_call
from openearth.errors import EmptyCollectionError, JobError
from openearth.providers import get_collection
from openearth.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from openearth.geometry import ROI

# Days per Earth Engine chunk. One getInfo per chunk; smaller chunks mean more
# round-trips, larger ones risk the 5000-element/compute limits on dense
# collections. 90 days is the settled balance (plan.md).
CHUNK_DAYS = 90

# Cap the single-scene pixel count so a coarse reduceRegion stays cheap; the
# reducer runs bestEffort so it degrades gracefully rather than erroring.
_MAX_PIXELS = int(1e8)


@dataclass(frozen=True)
class SceneValue:
    """One scene's spatial reduction over the ROI: a mean and a pixel count."""

    timestamp: datetime
    value: float | None
    count: float


# ── pure helpers ─────────────────────────────────────────────


def chunk_ranges(start: date, end: date, max_days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """Half-open ``[start, end)`` sliced into contiguous ≤ *max_days* chunks.

    Chunk boundaries are day-aligned, so a calendar date's scenes never split
    across two chunks (Earth Engine ``filterDate`` is itself half-open). An
    empty or reversed range yields no chunks.
    """
    if max_days <= 0:
        raise ValueError(f"max_days must be positive; got {max_days}.")
    step = timedelta(days=max_days)
    ranges: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        ranges.append((cursor, nxt))
        cursor = nxt
    return ranges


def aggregate_daily(rows: list[SceneValue]) -> pd.DataFrame:
    """Collapse per-scene reductions into one row per UTC date.

    Daily value is the count-weighted mean of the scene means (more pixels →
    more weight); daily count is the pixel-count sum. Scenes with a ``None``
    value (fully masked over the ROI) are dropped. The frame is indexed by a
    tz-naive ``DatetimeIndex`` named ``date`` with ``value``/``count`` columns.
    """
    valid = [r for r in rows if r.value is not None]
    if not valid:
        return _empty_frame()

    by_date: dict[date, list[SceneValue]] = defaultdict(list)
    for row in valid:
        by_date[row.timestamp.astimezone(UTC).date()].append(row)

    dates = sorted(by_date)
    values: list[float] = []
    counts: list[int] = []
    for day in dates:
        group = by_date[day]
        weights = np.array([r.count for r in group], dtype=float)
        means = np.array([r.value for r in group], dtype=float)
        total = float(weights.sum())
        # A zero total weight (all scenes reported 0 pixels yet a non-null
        # mean) is degenerate; fall back to an unweighted mean rather than 0/0.
        daily_value = float((means * weights).sum() / total) if total > 0 else float(means.mean())
        values.append(daily_value)
        counts.append(int(total))

    index = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name="date")
    return pd.DataFrame({"value": values, "count": counts}, index=index)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"value": pd.Series(dtype="float64"), "count": pd.Series(dtype="int64")},
        index=pd.DatetimeIndex([], name="date"),
    )


# ── Earth Engine engine ──────────────────────────────────────


def _make_feature_fn(roi: ROI, band: str, scale_m: int) -> Callable[[ee.Image], ee.Feature]:
    """Build the server-side per-image reducer.

    Every Earth Engine object is constructed *inside* the returned closure,
    which real ``ImageCollection.map`` invokes once to trace the computation
    but a faked collection never calls — so the engine imports and runs with
    no live EE session (offline tests fake ``get_collection``/``ee_call``).
    """

    def _feature(image: ee.Image) -> ee.Feature:
        stats = (
            image.select([band])
            .rename(["value"])
            .reduceRegion(
                reducer=ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True),
                geometry=roi.to_ee_geometry(),
                scale=scale_m,
                bestEffort=True,
                maxPixels=_MAX_PIXELS,
            )
        )
        return ee.Feature(
            None,
            {
                "t": image.get("system:time_start"),
                "mean": stats.get("value_mean"),
                "count": stats.get("value_count"),
            },
        )

    return _feature


def _parse_features(payload: dict[str, Any] | None) -> list[SceneValue]:
    """Turn a reduced FeatureCollection ``getInfo`` payload into scene rows."""
    rows: list[SceneValue] = []
    for feature in (payload or {}).get("features", []):
        props = feature.get("properties", {})
        timestamp_ms = props.get("t")
        if timestamp_ms is None:
            continue
        mean = props.get("mean")
        rows.append(
            SceneValue(
                timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
                value=None if mean is None else float(mean),
                count=float(props.get("count") or 0),
            )
        )
    return rows


def _raise_if_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise JobError("cancelled")


def daily_timeseries(
    data_key: str,
    source: str,
    roi: ROI,
    start: date,
    end: date,
    *,
    scale_m: int | None = None,
    on_chunk: Callable[[int, int, pd.DataFrame], None] | None = None,
    cancel: threading.Event | None = None,
) -> pd.DataFrame:
    """Reduce a data product to a daily time series over *roi* and *[start, end)*.

    Chunks are reduced concurrently in a thread pool sized to the shared EE
    concurrency budget (the core semaphore arbitrates against tile mints). As
    each chunk lands, ``on_chunk(done, total, chunk_frame)`` fires for
    progressive delivery. ``cancel`` is polled before dispatch and between
    chunk completions; when set the engine raises ``JobError("cancelled")``.

    An entirely empty result (no scenes, or every scene masked) is honest
    output, not an error — the returned frame is simply empty. RGB products
    have no scalar band to reduce and are refused with ``ValueError``.
    """
    dataset_id, product = resolve_product(data_key, source)
    if product.is_rgb:
        raise ValueError(
            f"Timeseries requires a scalar product; {product.key!r} is an RGB composite."
        )
    effective_scale = scale_m if scale_m is not None else get_dataset(dataset_id).default_scale_m

    chunks = chunk_ranges(start, end, CHUNK_DAYS)
    total = len(chunks)
    if total == 0:
        return _empty_frame()

    feature_fn = _make_feature_fn(roi, product.band, effective_scale)

    def _reduce_chunk(bounds: tuple[date, date]) -> list[SceneValue]:
        chunk_start, chunk_end = bounds
        try:
            collection = get_collection(
                data_key, roi, chunk_start.isoformat(), chunk_end.isoformat(), source
            )
        except EmptyCollectionError:
            return []
        return _parse_features(ee_call(collection.map(feature_fn).getInfo))

    _raise_if_cancelled(cancel)

    frames: list[pd.DataFrame] = []
    done = 0
    workers = max(1, get_settings().ee_max_concurrency)
    pending: set[Future[list[SceneValue]]] = set()
    remaining = iter(chunks)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        # Bounded dispatch: keep at most `workers` chunks in flight and check
        # cancellation before submitting each one, so a cancel between chunk
        # completions genuinely stops the remaining round-trips (a freed pool
        # worker must not pull the next queued chunk before we can react).
        def _dispatch(n: int) -> None:
            for bounds in itertools.islice(remaining, n):
                _raise_if_cancelled(cancel)
                pending.add(pool.submit(_reduce_chunk, bounds))

        _dispatch(workers)
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                _raise_if_cancelled(cancel)
                chunk_frame = aggregate_daily(future.result())
                frames.append(chunk_frame)
                done += 1
                if on_chunk is not None:
                    on_chunk(done, total, chunk_frame)
            _dispatch(len(finished))
    finally:
        # cancel_futures drops chunks that never started; running ones unwind.
        pool.shutdown(wait=True, cancel_futures=True)

    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        return _empty_frame()
    return pd.concat(non_empty).sort_index()


__all__ = ["CHUNK_DAYS", "SceneValue", "aggregate_daily", "chunk_ranges", "daily_timeseries"]
