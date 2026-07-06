"""Reference-event ingestion + detection cross-match (pure — no Earth Engine).

Public emission inventories (IMEO, SRON) list observed methane point sources.
:func:`parse_events` reads a tolerant CSV or a GeoJSON of such events;
:func:`match_detection` decides whether a detection coincides with one in space
and time. Everything here is deterministic and unit-tested offline; mypy strict,
no exemptions.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

# Column-name aliases (matched case-insensitively) for tolerant CSV/GeoJSON.
_LAT_KEYS = ("lat", "latitude")
_LON_KEYS = ("lon", "lng", "longitude")
_DATE_KEYS = ("date", "datetime", "detection_date", "time", "event_time_utc")
_RATE_KEYS = ("rate", "q", "source_rate_t_h", "q_t_h", "emission_rate")
_SIGMA_KEYS = ("sigma", "uncertainty", "q_sigma", "error")

# Published emission rates are conventionally tonnes/hour; we store kg/h.
_T_PER_H_TO_KG_PER_H = 1000.0

# Verdict windows (days) around the detection time.
_CONFIRMED_DAYS = 14
_PLAUSIBLE_DAYS = 60

_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class ReferenceEvent:
    """One reference emission event (mirrors the DB row, minus ids)."""

    source: str
    event_time_utc: str  # ISO-8601 UTC
    lat: float
    lon: float
    q_kg_h: float | None
    q_sigma_kg_h: float | None
    raw: dict[str, Any]


def _lookup(row: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _to_iso_utc(value: Any) -> str | None:
    """Parse a date/datetime string to an ISO-8601 UTC string, or None."""
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _float_or_none(value: Any, *, scale: float = 1.0) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value) * scale
    except (TypeError, ValueError):
        return None


def _event_from_fields(row: dict[str, Any], source: str) -> ReferenceEvent | None:
    """Build a ReferenceEvent from an alias-normalized mapping, or None if invalid."""
    lat = _float_or_none(_lookup(row, _LAT_KEYS))
    lon = _float_or_none(_lookup(row, _LON_KEYS))
    when = _lookup(row, _DATE_KEYS)
    iso = _to_iso_utc(when) if when is not None else None
    if lat is None or lon is None or iso is None:
        return None
    return ReferenceEvent(
        source=source,
        event_time_utc=iso,
        lat=lat,
        lon=lon,
        q_kg_h=_float_or_none(_lookup(row, _RATE_KEYS), scale=_T_PER_H_TO_KG_PER_H),
        q_sigma_kg_h=_float_or_none(_lookup(row, _SIGMA_KEYS), scale=_T_PER_H_TO_KG_PER_H),
        raw={k: v for k, v in row.items() if v not in (None, "")},
    )


def parse_events(
    data: bytes, *, fmt: Literal["csv", "geojson"], source: str
) -> list[ReferenceEvent]:
    """Parse *data* into reference events. Unparseable rows are skipped, not fatal."""
    if fmt == "csv":
        return _parse_csv(data, source)
    return _parse_geojson(data, source)


def _parse_csv(data: bytes, source: str) -> list[ReferenceEvent]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    events: list[ReferenceEvent] = []
    for raw_row in reader:
        row = {(k or "").strip().lower(): v for k, v in raw_row.items()}
        event = _event_from_fields(row, source)
        if event is not None:
            events.append(event)
    return events


def _parse_geojson(data: bytes, source: str) -> list[ReferenceEvent]:
    doc = json.loads(data.decode("utf-8"))
    features = doc.get("features", []) if isinstance(doc, dict) else []
    events: list[ReferenceEvent] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        coords = geometry.get("coordinates") or [None, None]
        props = {str(k).strip().lower(): v for k, v in (feature.get("properties") or {}).items()}
        props.setdefault("lon", coords[0])
        props.setdefault("lat", coords[1])
        event = _event_from_fields(props, source)
        if event is not None:
            events.append(event)
    return events


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lon/lat points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def match_detection(
    det_lat: float,
    det_lon: float,
    det_time: datetime,
    events: Sequence[ReferenceEvent],
    *,
    max_km: float = 15.0,
) -> tuple[str, list[int]]:
    """Cross-match a detection against reference *events*.

    Returns ``(verdict, matched_indices)``:
      - ``'confirmed'``  — a reference event within *max_km* AND ±14 days,
      - ``'plausible'``  — within *max_km* AND ±60 days,
      - ``'unvalidated'`` otherwise.
    ``'contradicted'`` is never assigned automatically (a human PATCH only).
    ``matched_indices`` indexes the supporting events (within *max_km* and the
    plausible window); the caller maps them to persisted event ids.
    """
    if det_time.tzinfo is None:
        det_time = det_time.replace(tzinfo=UTC)

    matched: list[int] = []
    within_confirmed = False
    for idx, event in enumerate(events):
        if haversine_km(det_lat, det_lon, event.lat, event.lon) > max_km:
            continue
        event_time = datetime.fromisoformat(event.event_time_utc)
        delta_days = abs((det_time - event_time).total_seconds()) / 86400.0
        if delta_days <= _PLAUSIBLE_DAYS:
            matched.append(idx)
            if delta_days <= _CONFIRMED_DAYS:
                within_confirmed = True

    if within_confirmed:
        return ("confirmed", matched)
    if matched:
        return ("plausible", matched)
    return ("unvalidated", [])
