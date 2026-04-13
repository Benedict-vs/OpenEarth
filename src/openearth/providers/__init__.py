"""Data provider modules and shared configuration helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import ee

from openearth.providers.gee_s1 import get_s1_collection
from openearth.providers.gee_s2 import get_s2_collection
from openearth.providers.gee_s5p import get_trace_gas_collection
from openearth.providers.s1_registry import (
    S1BandConfig,
    get_s1_band_config,
)
from openearth.providers.s2_registry import (
    S2IndexConfig,
    get_s2_index_config,
)
from openearth.providers.s5p_registry import (
    GasConfig,
    get_gas_config,
)


_S1_KEYS = {"VV", "VH", "VV_VH_RATIO", "RVI"}


def _resolve_source(data_key: str, source: str) -> str:
    """Resolve the ``"methane"`` sentinel to ``"s5p"``, ``"s1"``, or ``"s2"``."""
    if source == "methane":
        if data_key == "CH4":
            return "s5p"
        if data_key in _S1_KEYS:
            return "s1"
        return "s2"
    return source


def get_config(
    data_key: str, source: str,
) -> S1BandConfig | S2IndexConfig | GasConfig:
    """Return the registry config for *data_key*."""
    source = _resolve_source(data_key, source)
    if source == "s1":
        return get_s1_band_config(data_key)
    if source == "s2":
        return get_s2_index_config(data_key)
    return get_gas_config(data_key)


def get_collection(
    data_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str,
) -> ee.ImageCollection:
    """Return the filtered ImageCollection for *source*."""
    source = _resolve_source(data_key, source)
    if source == "s1":
        return get_s1_collection(
            data_key, geometry,
            start_date, end_date,
        )
    if source == "s2":
        return get_s2_collection(
            data_key, geometry,
            start_date, end_date,
        )
    return get_trace_gas_collection(
        data_key, geometry,
        start_date, end_date,
    )


def list_acquisition_times(
    data_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str,
) -> list[datetime]:
    """Return sorted UTC datetimes for every image in the collection."""
    collection = get_collection(
        data_key, geometry,
        start_date, end_date, source,
    )
    timestamps_ms: list[int] = (
        collection
        .aggregate_array("system:time_start")
        .getInfo()
    )
    if not timestamps_ms:
        return []
    return sorted(
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        for ms in timestamps_ms
    )


def get_single_image(
    data_key: str,
    geometry: ee.Geometry,
    timestamp_ms: int,
    source: str,
) -> ee.Image:
    """Return one image matching *timestamp_ms* exactly."""
    # Build a 1-day window to keep the collection query efficient.
    centre = datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc,
    )
    start = (centre - timedelta(hours=12)).isoformat()
    end = (centre + timedelta(hours=12)).isoformat()

    collection = get_collection(
        data_key, geometry, start, end, source,
    )
    image = (
        collection
        .filter(
            ee.Filter.eq(
                "system:time_start", timestamp_ms,
            ),
        )
        .first()
    )
    return image
