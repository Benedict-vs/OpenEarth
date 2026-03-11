"""Build daily trace-gas ROI time series from Earth Engine."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import ee
import pandas as pd

from openearth.analytics.conversions import to_ee_date
from openearth.providers.gas_registry import get_gas_config
from openearth.providers.gee_trace_gas import (
    get_trace_gas_collection,
)

DEFAULT_SCALE_METERS = 11_132
DEFAULT_MAX_PIXELS = 1_000_000_000
_BATCH_SIZE = 10

_RESULT_COLUMNS = [
    "date",
    "value",
    "n_images",
    "valid_pixel_count",
    "total_pixel_count",
    "coverage_fraction",
]


def _rows_from_fc_info(
    info: Any,
) -> list[dict[str, Any]]:
    """Extract row dicts from a FeatureCollection info."""
    if not isinstance(info, dict):
        return []
    raw = info.get("features")
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for feat in raw:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties")
        if isinstance(props, dict):
            rows.append(props)
    return rows


def build_daily_timeseries(
    gas_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    scale_meters: int = DEFAULT_SCALE_METERS,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    best_effort: bool = True,
    batch_size: int = _BATCH_SIZE,
) -> pd.DataFrame:
    """Compute daily statistics for *gas_key* over an ROI.

    Days are processed in batches to stay within the Earth
    Engine concurrent-aggregation limit.  Each day uses a
    single combined ``mean + count`` reducer so that only
    **one** ``reduceRegion`` call is issued per day.

    ``total_pixel_count`` and ``n_days`` are fetched eagerly
    so they never add server-side aggregation pressure inside
    the mapped function.
    """
    config = get_gas_config(gas_key)
    band = config.band

    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    collection = get_trace_gas_collection(
        gas_key, geometry, start_date, end_date,
    )

    # ── Eager fetches (one getInfo each) ──────────────
    total_px: int = (
        ee.Image.constant(1)
        .rename("ones")
        .clip(geometry)
        .reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=geometry,
            scale=scale_meters,
            maxPixels=max_pixels,
            bestEffort=best_effort,
        )
        .get("ones")
        .getInfo()
    ) or 0

    n_days: int = (
        end.difference(start, "day")
        .toInt()
        .getInfo()
    )
    if not isinstance(n_days, int) or n_days <= 0:
        return pd.DataFrame(columns=_RESULT_COLUMNS)

    # Combined reducer: one reduceRegion per day.
    # Output keys: {band}_mean  and  {band}_count
    combined = ee.Reducer.mean().combine(
        reducer2=ee.Reducer.count(),
        sharedInputs=True,
    )
    mean_key = f"{band}_mean"
    count_key = f"{band}_count"

    def _build_day_feature(
        day_offset: ee.Number,
    ) -> ee.Feature:
        day_offset = ee.Number(day_offset)
        day_start = start.advance(
            day_offset, "day",
        )
        day_end = day_start.advance(1, "day")
        daily = collection.filterDate(
            day_start, day_end,
        )
        n_images = daily.size()

        def with_data() -> ee.Dictionary:
            img = ee.Image(
                daily.mean(),
            ).select(band)
            stats = img.reduceRegion(
                reducer=combined,
                geometry=geometry,
                scale=scale_meters,
                maxPixels=max_pixels,
                bestEffort=best_effort,
            )
            return ee.Dictionary({
                "date": day_start.format(
                    "YYYY-MM-dd",
                ),
                "value": stats.get(mean_key),
                "n_images": n_images,
                "valid_pixel_count": stats.get(
                    count_key,
                ),
            })

        def without_data() -> ee.Dictionary:
            return ee.Dictionary({
                "date": day_start.format(
                    "YYYY-MM-dd",
                ),
                "value": None,
                "n_images": 0,
                "valid_pixel_count": 0,
            })

        properties = ee.Dictionary(
            ee.Algorithms.If(
                n_images.gt(0),
                with_data(),
                without_data(),
            )
        )
        return ee.Feature(None, properties)

    # ── Process in batches ────────────────────────────
    all_rows: list[dict[str, Any]] = []
    for batch_start in range(
        0, n_days, batch_size,
    ):
        batch_end = min(
            batch_start + batch_size, n_days,
        )
        offsets = ee.List(
            list(range(batch_start, batch_end)),
        )
        batch_fc = ee.FeatureCollection(
            offsets.map(_build_day_feature),
        )
        info = batch_fc.getInfo()
        all_rows.extend(_rows_from_fc_info(info))

    if not all_rows:
        return pd.DataFrame(columns=_RESULT_COLUMNS)

    # ── Build DataFrame, compute coverage client-side ─
    df = pd.DataFrame(all_rows)
    df["total_pixel_count"] = total_px
    valid = pd.to_numeric(
        df["valid_pixel_count"], errors="coerce",
    ).fillna(0)
    df["coverage_fraction"] = (
        valid / total_px if total_px > 0 else 0.0
    )
    return df.sort_values("date").reset_index(
        drop=True,
    )
