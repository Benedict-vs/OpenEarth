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
_DATE_KEYS = ("date", "datetime", "detection_date", "time", "event_time_utc", "tile_date")

# Rate/sigma column aliases split by the unit the *column name* declares — so we
# never guess (fix 13 / Tier 3 P3). We store kg/h internally:
#   • t/h columns (SRON `source_rate_t_h`) are scaled ×1000;
#   • kg/h columns (UNEP-IMEO MARS `ch4_fluxrate`/`ch4_fluxrate_std`, verified
#     kg CH₄/h) pass through ×1;
#   • unit-agnostic columns (`rate`/`q`/`emission_rate`) carry no unit in their
#     name — scaled only when the caller passes an explicit ``unit=``, otherwise
#     dropped to None (a wrong rate is worse than a missing one; cross-match
#     verdicts never used the rate — only space + time).
# Any row key ending in a unit suffix (`_t_h`/`_th` or `_kg_h`/`_kgh`) is treated
# as unit-declared too, so `flux_kg_h` self-describes.
_RATE_KEYS_KGH_NAMED = ("ch4_fluxrate",)
_RATE_KEYS_AGNOSTIC = ("rate", "q", "emission_rate")
_SIGMA_KEYS_KGH_NAMED = ("ch4_fluxrate_std",)
_SIGMA_KEYS_AGNOSTIC = ("sigma", "uncertainty", "q_sigma", "error")
_TH_SUFFIXES = ("_t_h", "_th")
_KGH_SUFFIXES = ("_kg_h", "_kgh")

# Published emission rates are conventionally tonnes/hour; we store kg/h.
_T_PER_H_TO_KG_PER_H = 1000.0

# Sanity bound: reject an absurd parsed rate (unit error, e.g. kg/h read as t/h).
# Published S2-scale point-source rates top out far below 500 t/h; a value above
# it drops the *rate* to None (the event still imports and cross-matches).
_MAX_RATE_KG_H = 500.0 * _T_PER_H_TO_KG_PER_H

RateUnit = Literal["t_h", "kg_h", "auto"]

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
    # Unit the stored rate was derived from ("t_h"/"kg_h"), or None when no rate
    # was resolved (no rate column, ambiguous unit, or the sanity guard dropped
    # it). Mirrored into ``raw`` so it survives into the DB row's raw JSON — the
    # only place unit provenance lives (no schema/DB migration this phase).
    rate_unit: str | None = None


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


def _looks_like_sigma(key: str) -> bool:
    """A column that names an uncertainty, not a rate — never grabbed as a rate."""
    return (
        key in _SIGMA_KEYS_KGH_NAMED
        or key in _SIGMA_KEYS_AGNOSTIC
        or any(tok in key for tok in ("sigma", "uncertainty", "error", "_std"))
    )


def _declared_unit(key: str) -> str | None:
    """The unit a column *name* declares, or None if it is unit-agnostic."""
    if key in _RATE_KEYS_KGH_NAMED or key.endswith(_KGH_SUFFIXES):
        return "kg_h"
    if key.endswith(_TH_SUFFIXES):
        return "t_h"
    return None


def _scale_to_kg_h(value: float | None, unit: str) -> tuple[float | None, str, str | None]:
    """Scale a parsed rate to kg/h, applying the sanity guard.

    Returns ``(q_kg_h, unit, dropped_reason)`` — ``dropped_reason`` is non-None
    (and ``q_kg_h`` None) when the guard fires; ``unit`` is always the detected
    unit, kept for diagnosis even on a drop.
    """
    if value is None:
        return None, unit, None
    kg_h = value * (_T_PER_H_TO_KG_PER_H if unit == "t_h" else 1.0)
    if abs(kg_h) > _MAX_RATE_KG_H:
        return None, unit, "over_500_th_guard"
    return kg_h, unit, None


def _resolve_rate(
    row: dict[str, Any], unit: RateUnit
) -> tuple[float | None, str | None, str | None]:
    """Resolve the row's rate to kg/h without guessing units.

    Returns ``(q_kg_h, rate_unit, dropped_reason)``. Unit-declared columns win
    over unit-agnostic ones and over the ``unit=`` param. An agnostic-only row is
    scaled by an explicit ``unit=`` or, under ``"auto"``, dropped (rate_unit None).
    """
    for key, val in row.items():
        if val in (None, "") or _looks_like_sigma(key):
            continue
        declared = _declared_unit(key)
        if declared is not None:
            return _scale_to_kg_h(_float_or_none(val), declared)
    for key in _RATE_KEYS_AGNOSTIC:
        val = row.get(key)
        if val in (None, ""):
            continue
        if unit == "auto":
            return None, None, "ambiguous_unit"  # never guessed
        return _scale_to_kg_h(_float_or_none(val), unit)
    return None, None, None  # no rate column present


def _resolve_sigma(row: dict[str, Any], rate_unit: str | None) -> float | None:
    """Resolve the row's sigma to kg/h. Unit-declared sigma columns use their own
    unit; an agnostic sigma inherits the resolved *rate's* unit (None → drop)."""
    for key in _SIGMA_KEYS_KGH_NAMED:
        val = row.get(key)
        if val not in (None, ""):
            return _float_or_none(val)  # kg/h
    for key, val in row.items():
        if val in (None, "") or not _looks_like_sigma(key):
            continue
        if key.endswith(_KGH_SUFFIXES):
            return _float_or_none(val)
        if key.endswith(_TH_SUFFIXES):
            return _float_or_none(val, scale=_T_PER_H_TO_KG_PER_H)
    for key in _SIGMA_KEYS_AGNOSTIC:
        val = row.get(key)
        if val in (None, ""):
            continue
        if rate_unit == "t_h":
            return _float_or_none(val, scale=_T_PER_H_TO_KG_PER_H)
        if rate_unit == "kg_h":
            return _float_or_none(val)
        return None  # rate unit unknown → sigma unit unknown
    return None


def _event_from_fields(row: dict[str, Any], source: str, unit: RateUnit) -> ReferenceEvent | None:
    """Build a ReferenceEvent from an alias-normalized mapping, or None if invalid."""
    lat = _float_or_none(_lookup(row, _LAT_KEYS))
    lon = _float_or_none(_lookup(row, _LON_KEYS))
    when = _lookup(row, _DATE_KEYS)
    iso = _to_iso_utc(when) if when is not None else None
    if lat is None or lon is None or iso is None:
        return None
    q_kg_h, rate_unit, dropped = _resolve_rate(row, unit)
    sigma = _resolve_sigma(row, rate_unit)
    raw = {k: v for k, v in row.items() if v not in (None, "")}
    raw["rate_unit"] = rate_unit  # provenance → DB raw JSON
    if dropped is not None:
        raw["rate_dropped"] = dropped  # a rate was present but not stored
    return ReferenceEvent(
        source=source,
        event_time_utc=iso,
        lat=lat,
        lon=lon,
        q_kg_h=q_kg_h,
        q_sigma_kg_h=sigma,
        raw=raw,
        rate_unit=rate_unit,
    )


def parse_events(
    data: bytes, *, fmt: Literal["csv", "geojson"], source: str, unit: RateUnit = "auto"
) -> list[ReferenceEvent]:
    """Parse *data* into reference events. Unparseable rows are skipped, not fatal.

    *unit* applies only to unit-agnostic rate columns (`rate`/`q`/`emission_rate`);
    unit-declared columns (`*_t_h`, `ch4_fluxrate`/`*_kg_h`) always self-describe.
    """
    if fmt == "csv":
        return _parse_csv(data, source, unit)
    return _parse_geojson(data, source, unit)


def _parse_csv(data: bytes, source: str, unit: RateUnit) -> list[ReferenceEvent]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    events: list[ReferenceEvent] = []
    for raw_row in reader:
        row = {(k or "").strip().lower(): v for k, v in raw_row.items()}
        event = _event_from_fields(row, source, unit)
        if event is not None:
            events.append(event)
    return events


def _parse_geojson(data: bytes, source: str, unit: RateUnit) -> list[ReferenceEvent]:
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
        event = _event_from_fields(props, source, unit)
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
