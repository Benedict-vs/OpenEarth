"""Data provider modules and shared configuration helpers."""

from __future__ import annotations

from datetime import date, datetime

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


def _resolve_source(data_key: str, source: str) -> str:
    """Resolve the ``"methane"`` sentinel to ``"s5p"`` or ``"s2"``."""
    if source == "methane":
        return "s5p" if data_key == "CH4" else "s2"
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
