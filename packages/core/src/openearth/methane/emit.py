"""EMIT methane plume complexes: GEE V001 query + V002 GeoJSON parser + cross-match.

EMIT L2B CH4PLM delivers one *plume complex* per granule — a plume outline plus,
in V002, an emission-rate estimate. Two sources, one model:

* **GEE V001 mirror** (``NASA/EMIT/L2B/CH4PLM``, band ``methane_plume_complex``):
  a frozen copy covering 2022-08-10 → 2024-10-26. The outline is not the granule
  footprint — it is ``reduceToVectors`` over the positive-enhancement mask
  (``gt(0)``). No emission rate (V001 metadata only).
* **LP DAAC V002 GeoJSON** (fetched by the API's earthaccess fallback, Phase 6):
  the live collection past the GEE freeze. One feature per granule carries the
  outline, max-enhancement coords, and an emission rate + uncertainty.

Following the ``wind.py`` split, the pure halves — the V002 parser, the
date-router, and the cross-match — live in module-level functions and are
unit-tested offline; only :func:`list_plumes_gee` touches Earth Engine.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.ee.client import ee_call
from openearth.geometry import BBox
from openearth.methane.validation import haversine_km

CH4PLM_COLLECTION_ID = "NASA/EMIT/L2B/CH4PLM"
_PLM_BAND = "methane_plume_complex"
# EMIT products are 60 m; the plume rasters are tiny, so reduceToVectors is cheap.
_PLM_SCALE_M = 60

# The GEE CH4PLM mirror is a frozen V001 copy: its last granule is 2024-10-26.
# Windows entirely on/before this date are fully served by GEE; anything later
# needs the V002 (earthaccess) path. Callers split straddling windows here.
GEE_CH4PLM_CUTOFF = date(2024, 10, 26)


@dataclass(frozen=True)
class EmitPlume:
    """One EMIT methane plume complex, source-agnostic.

    ``outline`` is a GeoJSON *geometry* dict in EPSG:4326 (Polygon/MultiPolygon).
    Emission-rate fields are populated only from V002 GeoJSON; GEE V001 plumes
    carry ``None`` for both. ``provenance`` is ``"gee_v001"`` or ``"lpdaac_v002"``.
    """

    plume_id: str
    outline: dict[str, Any]
    time_utc: datetime
    max_enh_ppm_m: float | None
    max_enh_lat: float | None
    max_enh_lon: float | None
    q_kg_h: float | None
    q_sigma_kg_h: float | None
    provenance: str
    source_scenes: list[str]

    def representative_point(self) -> tuple[float, float] | None:
        """(lat, lon) for cross-matching: max-enhancement point, else the outline centroid."""
        if self.max_enh_lat is not None and self.max_enh_lon is not None:
            return (self.max_enh_lat, self.max_enh_lon)
        return _centroid_latlon(self.outline)


@dataclass(frozen=True)
class EmitMatch:
    """A detection's cross-match to one EMIT plume, with the space/time offsets."""

    plume: EmitPlume
    distance_km: float
    dt_days: float  # signed: plume time − detection time, in days


# ── V002 GeoJSON parser (pure, offline-tested against a fixture) ──────────────

# Exact V002 feature property keys (verified against the JPL portal aggregate and
# emit-sds/emit-ghg delivery_plume_tiler.py). Missing numerics arrive as "NA".
_KEY_PLUME_ID = "Plume ID"
_KEY_TIME = "UTC Time Observed"
_KEY_MAX_ENH = "Max Plume Concentration (ppm m)"
_KEY_MAX_LAT = "Latitude of max concentration"
_KEY_MAX_LON = "Longitude of max concentration"
_KEY_Q = "Emissions Rate Estimate (kg/hr)"
_KEY_Q_SIGMA = "Emissions Rate Estimate Uncertainty (kg/hr)"
# DAAC delivery renames the source-scene list; the portal aggregate uses FIDs.
_KEY_DAAC_SCENES = "DAAC Scene Names"
_KEY_SCENE_FIDS = "Scene FIDs"


def _na_float(value: Any) -> float | None:
    """Coerce a V002 numeric to float, treating "NA"/absent/empty as None."""
    if value is None or value in ("NA", "N/A", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_v002_time(value: Any) -> datetime | None:
    """Parse ``"2025-09-22T20:49:33Z"`` → tz-aware UTC datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _scene_list(props: dict[str, Any]) -> list[str]:
    """Source scenes: DAAC-delivered names if present, else the portal FIDs."""
    for key in (_KEY_DAAC_SCENES, _KEY_SCENE_FIDS):
        value = props.get(key)
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str) and value not in ("", "NA"):
            return [value]
    return []


def _plume_from_feature(feature: dict[str, Any]) -> EmitPlume | None:
    """Build an EmitPlume from one V002 feature, or None if it is not a plume outline.

    Tolerant per the DAAC/portal schema variance: required = Plume ID, a parseable
    UTC time, and a Polygon/MultiPolygon geometry; everything else is optional with
    ``"NA"``/absent → None. Point features (portal max-enhancement markers) are skipped.
    """
    geometry = feature.get("geometry") or {}
    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    props = feature.get("properties") or {}
    plume_id = props.get(_KEY_PLUME_ID)
    time_utc = _parse_v002_time(props.get(_KEY_TIME))
    if not plume_id or time_utc is None:
        return None
    return EmitPlume(
        plume_id=str(plume_id),
        outline=geometry,
        time_utc=time_utc,
        max_enh_ppm_m=_na_float(props.get(_KEY_MAX_ENH)),
        max_enh_lat=_na_float(props.get(_KEY_MAX_LAT)),
        max_enh_lon=_na_float(props.get(_KEY_MAX_LON)),
        q_kg_h=_na_float(props.get(_KEY_Q)),
        q_sigma_kg_h=_na_float(props.get(_KEY_Q_SIGMA)),
        provenance="lpdaac_v002",
        source_scenes=_scene_list(props),
    )


def parse_v002_geojson(data: bytes) -> list[EmitPlume]:
    """Parse an EMIT V002 CH4PLM GeoJSON (FeatureCollection or bare Feature) to plumes.

    Accepts both the multi-feature JPL portal aggregate and a single-feature DAAC
    granule. Non-plume features (points, malformed rows) are skipped, not fatal.
    """
    doc = json.loads(data.decode("utf-8"))
    if not isinstance(doc, dict):
        return []
    if doc.get("type") == "Feature":
        features: list[Any] = [doc]
    else:
        raw = doc.get("features")
        features = raw if isinstance(raw, list) else []
    plumes: list[EmitPlume] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        plume = _plume_from_feature(feature)
        if plume is not None:
            plumes.append(plume)
    return plumes


# ── Pure geometry / dedup / cross-match ──────────────────────────────────────


def _iter_coords(node: Any) -> Iterable[tuple[float, float]]:
    """Yield every (lon, lat) leaf pair in a nested GeoJSON coordinate array."""
    if (
        isinstance(node, (list, tuple))
        and len(node) >= 2
        and all(isinstance(c, (int, float)) for c in node[:2])
    ):
        yield (float(node[0]), float(node[1]))
        return
    if isinstance(node, (list, tuple)):
        for child in node:
            yield from _iter_coords(child)


def _centroid_latlon(geometry: dict[str, Any]) -> tuple[float, float] | None:
    """Mean-of-vertices (lat, lon) of a GeoJSON geometry — a match representative point."""
    coords = list(_iter_coords(geometry.get("coordinates")))
    if not coords:
        return None
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return (lat, lon)


def dedup_plumes(plumes: Iterable[EmitPlume], *, max_km: float = 1.0) -> list[EmitPlume]:
    """Drop duplicate plumes across GEE/V002 paths (same instant + near-same location).

    Windows straddling the GEE freeze query both paths; the same physical plume can
    surface twice with different ids (V001 ``system:index`` vs V002 ``Plume ID``).
    Two plumes collapse when observed within one minute and *max_km* of each other;
    the V002 plume wins (it carries the emission rate). Input order is otherwise kept.
    """
    kept: list[EmitPlume] = []
    for plume in plumes:
        point = plume.representative_point()
        replaced = False
        for i, existing in enumerate(kept):
            other = existing.representative_point()
            same_time = abs((plume.time_utc - existing.time_utc).total_seconds()) <= 60.0
            close = (
                point is not None
                and other is not None
                and haversine_km(point[0], point[1], other[0], other[1]) <= max_km
            )
            if same_time and close:
                if existing.provenance == "gee_v001" and plume.provenance == "lpdaac_v002":
                    kept[i] = plume
                replaced = True
                break
        if not replaced:
            kept.append(plume)
    return kept


def cross_match(
    det_lat: float,
    det_lon: float,
    det_time_utc: datetime,
    plumes: Sequence[EmitPlume],
    *,
    max_km: float = 5.0,
    max_days: float = 3.0,
) -> list[EmitMatch]:
    """EMIT plumes coincident with a detection in space (*max_km*) and time (*max_days*).

    Each plume's location is its max-enhancement point (V002) or outline centroid
    (V001). Matches are sorted nearest-first, ties broken by smaller |Δt|.
    """
    if det_time_utc.tzinfo is None:
        det_time_utc = det_time_utc.replace(tzinfo=UTC)

    matches: list[EmitMatch] = []
    for plume in plumes:
        point = plume.representative_point()
        if point is None:
            continue
        distance_km = haversine_km(det_lat, det_lon, point[0], point[1])
        if distance_km > max_km:
            continue
        dt_days = (plume.time_utc - det_time_utc).total_seconds() / 86400.0
        if abs(dt_days) > max_days:
            continue
        matches.append(EmitMatch(plume=plume, distance_km=distance_km, dt_days=dt_days))

    matches.sort(key=lambda m: (m.distance_km, abs(m.dt_days)))
    return matches


# ── Date router (pure) ───────────────────────────────────────────────────────


def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def gee_available(start: str | date | datetime, end: str | date | datetime) -> bool:
    """True iff the whole ``[start, end]`` window is within the frozen GEE V001 mirror.

    A window ending on/before :data:`GEE_CH4PLM_CUTOFF` is served entirely by GEE.
    Straddling windows return False; the caller queries GEE for the part on/before
    the cutoff and the V002 path for the remainder, then de-duplicates.
    """
    return _as_date(end) <= GEE_CH4PLM_CUTOFF


# ── GEE V001 query (Earth Engine) ────────────────────────────────────────────


def list_plumes_gee(
    bbox: BBox,
    start: str | date | datetime,
    end: str | date | datetime,
) -> list[EmitPlume]:
    """Frozen GEE V001 CH4PLM plume complexes over *bbox* within ``[start, end]``.

    One granule = one plume complex. The outline comes from
    ``selfMask().reduceToVectors`` over the plume pixels (the granule footprint is
    far larger than the plume); the per-granule max enhancement rides along. All
    outlines are vectorized server-side and pulled in a single ``ee_call``.
    """
    geometry = bbox.to_ee_geometry()
    collection = (
        ee.ImageCollection(CH4PLM_COLLECTION_ID)
        .filterBounds(geometry)
        .filterDate(to_ee_date(start), to_ee_date(end))
    )

    def _to_outline(image: ee.Image) -> ee.Feature:
        image = ee.Image(image)
        band = image.select(_PLM_BAND)
        footprint = image.geometry()
        # reduceToVectors needs an *integral* first band, but methane_plume_complex
        # is float ppm·m carrying the full matched-filter field (negatives included)
        # cropped to the plume-complex tile. Vectorize the positive-enhancement mask
        # instead — that traces the plume, not the tile footprint.
        plume_mask = band.gt(0).selfMask()
        vectors = plume_mask.reduceToVectors(
            geometry=footprint,
            scale=_PLM_SCALE_M,
            geometryType="polygon",
            maxPixels=int(1e9),
            bestEffort=True,
        )
        max_enh = band.reduceRegion(
            reducer=ee.Reducer.max(),
            geometry=footprint,
            scale=_PLM_SCALE_M,
            maxPixels=int(1e9),
            bestEffort=True,
        ).get(_PLM_BAND)
        return ee.Feature(
            vectors.geometry(),
            {
                "plume_id": image.get("system:index"),
                "time_start": image.get("system:time_start"),
                "max_enh": max_enh,
            },
        )

    outlines = ee.FeatureCollection(collection.map(_to_outline))
    info = ee_call(outlines.getInfo) or {}
    return _plumes_from_gee_features(info.get("features", []))


def _plumes_from_gee_features(features: list[dict[str, Any]]) -> list[EmitPlume]:
    """Turn vectorized GEE outline features into EmitPlumes (pure — offline-testable)."""
    plumes: list[EmitPlume] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        props = feature.get("properties") or {}
        time_start = props.get("time_start")
        if time_start is None:
            continue
        time_utc = datetime.fromtimestamp(float(time_start) / 1000.0, tz=UTC)
        plumes.append(
            EmitPlume(
                plume_id=str(props.get("plume_id", "")),
                outline=geometry,
                time_utc=time_utc,
                max_enh_ppm_m=_na_float(props.get("max_enh")),
                max_enh_lat=None,
                max_enh_lon=None,
                q_kg_h=None,
                q_sigma_kg_h=None,
                provenance="gee_v001",
                source_scenes=[],
            )
        )
    return plumes
