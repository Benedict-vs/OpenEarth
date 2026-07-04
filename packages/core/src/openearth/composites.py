"""Composite builders: date-range mean, short-window, single scene, anomaly.

Ported from v1 ``visualization/heatmap.py`` with the folium layer removed.
Global-coverage detection is now pure client-side math on the ROI model
(v1 spent a ``getInfo`` round-trip on it).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import ee

from openearth.providers import get_collection, get_product_config, get_single_image
from openearth.providers.s2 import compute_methane_anomaly

if TYPE_CHECKING:
    from openearth.geometry import ROI


def _clip_unless_global(image: ee.Image, roi: ROI) -> ee.Image:
    """Skip the expensive server-side clip when the ROI is the whole planet."""
    if roi.is_global:
        return image
    return image.clip(roi.to_ee_geometry())


def build_mean_composite(
    data_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    source: str = "s5p",
) -> ee.Image:
    """Pixel-wise mean image over the full date range."""
    cfg = get_product_config(data_key, source)
    collection = get_collection(data_key, roi, start_date, end_date, source)
    image = collection.mean() if cfg.is_rgb else collection.mean().select(cfg.band)
    return _clip_unless_global(image, roi)


def build_date_composite(
    data_key: str,
    roi: ROI,
    target_date: str | date | datetime,
    half_window_days: int = 3,
    source: str = "s5p",
) -> ee.Image:
    """Short-window mean composite centred on *target_date*."""
    cfg = get_product_config(data_key, source)

    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    window_start = target_date - timedelta(days=half_window_days)
    window_end = target_date + timedelta(days=half_window_days + 1)

    collection = get_collection(
        data_key, roi, window_start.isoformat(), window_end.isoformat(), source
    )
    image = collection.mean() if cfg.is_rgb else collection.mean().select(cfg.band)
    return _clip_unless_global(image, roi)


def build_single_scene(
    data_key: str,
    roi: ROI,
    timestamp_ms: int,
    source: str = "s5p",
) -> ee.Image:
    """Return one scene (no aggregation) for *timestamp_ms*."""
    cfg = get_product_config(data_key, source)
    image = get_single_image(data_key, roi, timestamp_ms, source)
    if not cfg.is_rgb:
        image = image.select(cfg.band)
    return _clip_unless_global(image, roi)


def build_methane_anomaly_composite(
    roi: ROI,
    target_date: str | date | datetime,
    half_window_days: int,
    ref_start: str | date | datetime,
    ref_end: str | date | datetime,
) -> ee.Image:
    """Methane anomaly quicklook: target B12/B11 minus reference-period mean."""
    image = compute_methane_anomaly(roi, target_date, half_window_days, ref_start, ref_end)
    return _clip_unless_global(image, roi)
