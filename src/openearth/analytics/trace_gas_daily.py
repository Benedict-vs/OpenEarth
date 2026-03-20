"""Build daily ROI time series from Earth Engine."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import ee
import pandas as pd

from openearth.analytics.conversions import to_ee_date
from openearth.providers.gee_s2 import get_s2_collection
from openearth.providers.gee_s5p import (
    get_trace_gas_collection,
)
from openearth.providers.s2_registry import (
    get_s2_index_config,
)
from openearth.providers.s5p_registry import get_gas_config

DEFAULT_SCALE_METERS_S5P = 11_132
DEFAULT_SCALE_METERS_S2 = 500
DEFAULT_MAX_PIXELS = 1_000_000_000
BATCH_SIZE = 10

_RESULT_COLUMNS = [
    "date",
    "value",
    "n_images",
    "valid_pixel_count",
    "total_pixel_count",
    "coverage_fraction",
]


def _get_config(data_key: str, source: str):
    """Return the registry config for *data_key*."""
    if source == "s2":
        return get_s2_index_config(data_key)
    return get_gas_config(data_key)


def _get_collection(
    data_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str,
) -> ee.ImageCollection:
    """Return the ImageCollection for *source*."""
    if source == "s2":
        return get_s2_collection(
            data_key, geometry,
            start_date, end_date,
        )
    return get_trace_gas_collection(
        data_key, geometry,
        start_date, end_date,
    )


def _rows_from_fc_info(
    info: Any,
) -> list[dict[str, Any]]:
    """Extract row dicts from a FeatureCollection."""
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
    scale_meters: int | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    best_effort: bool = True,
    batch_size: int = BATCH_SIZE,
    source: str = "s5p",
) -> pd.DataFrame:
    """Compute daily statistics over an ROI.

    Days are processed in batches to stay within the
    Earth Engine concurrent-aggregation limit.  Each day
    uses a single combined ``mean + count`` reducer so
    that only **one** ``reduceRegion`` call is issued
    per day.

    Parameters
    ----------
    gas_key:
        Registry key (e.g. ``"NO2"`` for S5P,
        ``"NDVI"`` for S2).
    source:
        ``"s5p"`` for Sentinel-5P trace gases or
        ``"s2"`` for Sentinel-2 spectral indices.
    """
    config = _get_config(gas_key, source)
    band = config.band

    if scale_meters is None:
        scale_meters = (
            DEFAULT_SCALE_METERS_S2
            if source == "s2"
            else DEFAULT_SCALE_METERS_S5P
        )

    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    collection = _get_collection(
        gas_key, geometry,
        start_date, end_date, source,
    )

    # ── Eager fetches (one getInfo each) ──────────
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

    n_days_raw = (
        end.difference(start, "day")
        .toInt()
        .getInfo()
    )
    n_days: int = (
        int(n_days_raw)
        if n_days_raw is not None
        else 0
    )
    if n_days <= 0:
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

    # ── Process in batches ────────────────────────
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

    # ── Build DataFrame, compute coverage ─────────
    df = pd.DataFrame(all_rows)
    df["total_pixel_count"] = total_px
    valid = pd.to_numeric(
        df["valid_pixel_count"], errors="coerce",
    ).fillna(0)
    df["coverage_fraction"] = (
        valid / total_px if total_px > 0 else 0.0
    )

    # Drop days with no observations (value is None).
    # For S5P (daily global coverage) this is rare,
    # but S2 (~5-day revisit + cloud filter) produces
    # many empty days that would otherwise clutter the
    # time series and break statistics.
    df["value"] = pd.to_numeric(
        df["value"], errors="coerce",
    )
    df = df.dropna(subset=["value"])

    return df.sort_values("date").reset_index(
        drop=True,
    )
