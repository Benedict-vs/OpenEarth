"""Tier-1 S5P/TROPOMI screening: weekly XCH4 enhancement lattice + persistence.

Earth Engine does the bulk reduction (one ``reduceRegions`` per ISO week over a
cell lattice; ~1 ee_call/week plus a lazy background median); the flagging,
persistence counting and score ranking are pure functions on the returned
feature lists, unit-tested offline.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import ee
import numpy as np

from openearth.ee.client import ee_call
from openearth.errors import JobError
from openearth.methane.plume import robust_sigma
from openearth.providers.s5p import get_trace_gas_collection

if TYPE_CHECKING:
    from openearth.geometry import BBox

ProgressCallback = Callable[[int, int, str], None]

# The enhancement band name we reduce (mean reducer names its output by band).
_ENH_BAND = "ch4_enh"
# S5P L3 CH4 native grid ≈ 0.01° (~1.1 km).
_S5P_SCALE_M = 1113
# Screening lattice guard: refuse a bbox/cell_deg combo that would blow up the
# FeatureCollection (a browsing tier, not a per-pixel product).
_MAX_CELLS = 4000


@dataclass(frozen=True)
class Hotspot:
    """One persistently-enhanced screening cell."""

    lat: float
    lon: float
    mean_enh_ppb: float
    max_enh_ppb: float
    score: float  # mean_enh / robust σ of all cell-weeks
    weeks_flagged: int
    weeks_observed: int


@dataclass(frozen=True)
class _Cell:
    idx: int
    lon: float  # center
    lat: float  # center
    west: float
    south: float
    east: float
    north: float


def _cells(bbox: BBox, cell_deg: float) -> list[_Cell]:
    """Row-major NW→SE cell lattice over *bbox* (same layout as the wind grid)."""
    nx = max(1, round(bbox.width_deg / cell_deg))
    ny = max(1, round(bbox.height_deg / cell_deg))
    if nx * ny > _MAX_CELLS:
        raise ValueError(
            f"Screening lattice {nx}×{ny} exceeds {_MAX_CELLS} cells; "
            "use a larger cell_deg or a smaller bbox."
        )
    dx = bbox.width_deg / nx
    dy = bbox.height_deg / ny
    cells: list[_Cell] = []
    for row in range(ny):
        north = bbox.north - row * dy
        south = north - dy
        for col in range(nx):
            west = bbox.west + col * dx
            east = west + dx
            cells.append(
                _Cell(
                    idx=row * nx + col,
                    lon=(west + east) / 2.0,
                    lat=(south + north) / 2.0,
                    west=west,
                    south=south,
                    east=east,
                    north=north,
                )
            )
    return cells


def _week_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Split ``[start, end)`` into consecutive 7-day (ISO-week-length) buckets."""
    weeks: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=7), end)
        weeks.append((cursor, nxt))
        cursor = nxt
    return weeks


def _cell_features(cells: list[_Cell]) -> ee.FeatureCollection:
    return ee.FeatureCollection(
        [
            ee.Feature(ee.Geometry.Rectangle([c.west, c.south, c.east, c.north]), {"idx": c.idx})
            for c in cells
        ]
    )


def _reduce_week(
    bbox: BBox,
    background: ee.Image,
    week: tuple[date, date],
    cells_fc: ee.FeatureCollection,
) -> list[dict[str, Any]]:
    """One ee_call: weekly-mean minus background, reduced over the cell lattice."""
    week_mean = get_trace_gas_collection(
        "CH4", bbox, week[0].isoformat(), week[1].isoformat()
    ).mean()
    enh = week_mean.subtract(background).rename(_ENH_BAND)
    reduced = enh.reduceRegions(collection=cells_fc, reducer=ee.Reducer.mean(), scale=_S5P_SCALE_M)
    info = ee_call(reduced.getInfo) or {}
    return list(info.get("features", []))


def stitch_hotspots(
    weekly_features: list[list[dict[str, Any]]],
    cells: list[_Cell],
    *,
    sigma_thresh: float,
    top_n: int,
) -> list[Hotspot]:
    """Rank cells by persistent XCH4 enhancement (pure — no Earth Engine).

    A cell-week is *flagged* when its enhancement exceeds ``sigma_thresh`` times
    the robust σ of all observed cell-weeks; cells are scored by
    ``mean_enh / σ`` and the top ``top_n`` returned.
    """
    series: dict[int, list[float]] = {c.idx: [] for c in cells}
    for week in weekly_features:
        for feat in week:
            props = feat.get("properties", {})
            value = props.get(_ENH_BAND)
            if value is None:
                continue
            idx = int(props["idx"])
            if idx in series:
                series[idx].append(float(value))

    all_values = np.array([v for values in series.values() for v in values], dtype=float)
    if all_values.size == 0:
        return []
    sigma = robust_sigma(all_values)
    if not sigma or sigma <= 0.0:
        return []
    threshold = sigma_thresh * sigma

    by_idx = {c.idx: c for c in cells}
    hotspots: list[Hotspot] = []
    for idx, values in series.items():
        if not values:
            continue
        arr = np.array(values, dtype=float)
        cell = by_idx[idx]
        mean_enh = float(arr.mean())
        hotspots.append(
            Hotspot(
                lat=cell.lat,
                lon=cell.lon,
                mean_enh_ppb=mean_enh,
                max_enh_ppb=float(arr.max()),
                score=mean_enh / sigma,
                weeks_flagged=int((arr > threshold).sum()),
                weeks_observed=len(values),
            )
        )
    hotspots.sort(key=lambda h: h.score, reverse=True)
    return hotspots[:top_n]


def screen_region(
    bbox: BBox,
    start: date,
    end: date,
    *,
    background_days: int = 30,
    cell_deg: float = 0.05,
    sigma_thresh: float = 2.0,
    top_n: int = 50,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[Hotspot]:
    """Screen *bbox* over ``[start, end)`` for persistent XCH4 enhancements.

    The background is a per-pixel median over ``[start − background_days, start)``;
    each ISO week's mean minus that background is reduced over a ``cell_deg``
    lattice. Cancellable between weeks.
    """
    cells = _cells(bbox, cell_deg)
    cells_fc = _cell_features(cells)
    background = get_trace_gas_collection(
        "CH4", bbox, (start - timedelta(days=background_days)).isoformat(), start.isoformat()
    ).median()

    weeks = _week_ranges(start, end)
    weekly_features: list[list[dict[str, Any]]] = []
    for i, week in enumerate(weeks, start=1):
        if cancel is not None and cancel.is_set():
            raise JobError("cancelled")
        if on_progress is not None:
            on_progress(i, len(weeks), f"Week {i}/{len(weeks)}")
        weekly_features.append(_reduce_week(bbox, background, week, cells_fc))

    return stitch_hotspots(weekly_features, cells, sigma_thresh=sigma_thresh, top_n=top_n)
