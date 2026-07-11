"""EMIT plume services: GEE V001 query + earthaccess V002 fallback + detection cross-match.

Two source paths, one plume model (see ``openearth.methane.emit``): the frozen GEE
V001 mirror for windows on/before 2024-10-26, and the live LP DAAC V002 collection
(fetched via **earthaccess, lazy-imported here so ``create_app()`` stays credential-
free**) for anything later. Straddling windows query both and de-duplicate.

The core entry points (``list_plumes_gee``) are imported by name so offline tests
fake them at this module level; the earthaccess module is imported lazily inside
``_fetch_v002_plumes`` so tests inject a fake via ``sys.modules``. Only the GeoJSON
asset is fetched per V002 granule — never the COG (Phase 6 needs the outline + rate).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlmodel import Session

from openearth.geometry import BBox
from openearth.methane.emit import (
    GEE_CH4PLM_CUTOFF,
    EmitPlume,
    cross_match,
    dedup_plumes,
    list_plumes_gee,
    parse_v002_geojson,
)
from openearth_api.cache import cache_key, roi_key_part
from openearth_api.models import utcnow_iso
from openearth_api.schemas import (
    DetectionDetailOut,
    EmitMatchOut,
    EmitMatchResult,
    EmitPlumeOut,
    EmitPlumesOut,
)
from openearth_api.services.methane import (
    _detection_center,
    _require_detection,
    get_detection_detail,
)

if TYPE_CHECKING:
    import diskcache
    from sqlalchemy import Engine

_V002_SHORT_NAME = "EMITL2BCH4PLM"
_V002_VERSION = "002"
_V002_ASSET_INFIX = "CH4PLMMETA"  # the GeoJSON asset name infix (not the COG's CH4PLM)
_MAX_GRANULES = 200
# V002 is a living collection; a 1-day TTL keeps new plumes reasonably fresh.
_EMIT_TTL_SECONDS = 24 * 3600

# Detection cross-match: query a generous box/window around the detection, then let
# the core cross_match tighten to ≤5 km / ≤3 days.
_MATCH_HALF_DEG = 0.3
_MATCH_QUERY_DAYS = 7
_MATCH_MAX_KM = 5.0
_MATCH_MAX_DAYS = 3.0


def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


# ── Plume <-> schema conversion (cache stores plain JSON dicts, not dataclasses) ──


def _plume_to_out(plume: EmitPlume) -> EmitPlumeOut:
    return EmitPlumeOut(
        plume_id=plume.plume_id,
        outline=plume.outline,
        time_utc=plume.time_utc.isoformat(),
        provenance=plume.provenance,  # type: ignore[arg-type]
        max_enh_ppm_m=plume.max_enh_ppm_m,
        max_enh_lat=plume.max_enh_lat,
        max_enh_lon=plume.max_enh_lon,
        q_kg_h=plume.q_kg_h,
        q_sigma_kg_h=plume.q_sigma_kg_h,
        source_scenes=list(plume.source_scenes),
    )


def _out_to_plume(out: EmitPlumeOut) -> EmitPlume:
    return EmitPlume(
        plume_id=out.plume_id,
        outline=out.outline,
        time_utc=datetime.fromisoformat(out.time_utc),
        max_enh_ppm_m=out.max_enh_ppm_m,
        max_enh_lat=out.max_enh_lat,
        max_enh_lon=out.max_enh_lon,
        q_kg_h=out.q_kg_h,
        q_sigma_kg_h=out.q_sigma_kg_h,
        provenance=out.provenance,
        source_scenes=list(out.source_scenes),
    )


# ── earthaccess V002 fallback (lazy import; live path) ──


def _fetch_v002_plumes(bbox: BBox, start: str, end: str) -> list[EmitPlume]:
    """Search + fetch V002 CH4PLM GeoJSON granules over *bbox*/[start, end].

    Downloads only each granule's ``CH4PLMMETA`` JSON asset (never the COG). Missing
    or invalid Earthdata credentials surface as a 502 pointing at docs/deploy.md.
    """
    import earthaccess  # lazy: keeps create_app() and CI credential-free

    # Env vars first, ~/.netrc as the fallback — never "all", whose last resort
    # is an interactive prompt that would hang the worker thread.
    try:
        earthaccess.login(strategy="environment")
    except Exception:
        try:
            earthaccess.login(strategy="netrc")
        except Exception as exc:  # any auth failure → one actionable 502
            raise HTTPException(
                502,
                "EMIT V002 plumes need NASA Earthdata credentials — set EARTHDATA_TOKEN "
                "(or EARTHDATA_USERNAME/PASSWORD, or ~/.netrc). See docs/deploy.md.",
            ) from exc

    results = earthaccess.search_data(
        short_name=_V002_SHORT_NAME,
        version=_V002_VERSION,
        bounding_box=bbox.as_tuple(),  # (west, south, east, north)
        temporal=(start, end),
        count=_MAX_GRANULES,
    )
    session = earthaccess.get_requests_https_session()
    plumes: list[EmitPlume] = []
    for granule in results:
        for url in granule.data_links(access="out_of_region"):
            if _V002_ASSET_INFIX not in url:
                continue
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            plumes.extend(parse_v002_geojson(resp.content))
    return plumes


# ── Combined plume listing (window-split + dedup + cache) ──


def _compute_plumes(bbox: BBox, start: str, end: str) -> tuple[list[EmitPlume], list[str]]:
    """Query GEE for the frozen part, V002 for the live part; dedup the overlap."""
    start_d, end_d = _as_date(start), _as_date(end)
    plumes: list[EmitPlume] = []
    paths: list[str] = []

    if start_d <= GEE_CH4PLM_CUTOFF:
        gee_end = min(end_d, GEE_CH4PLM_CUTOFF)
        plumes.extend(list_plumes_gee(bbox, start, gee_end.isoformat()))
        paths.append("gee_v001")

    if end_d > GEE_CH4PLM_CUTOFF:
        v002_start = max(start_d, GEE_CH4PLM_CUTOFF + timedelta(days=1))
        plumes.extend(_fetch_v002_plumes(bbox, v002_start.isoformat(), end_d.isoformat()))
        paths.append("lpdaac_v002")

    return dedup_plumes(plumes), paths


def list_plumes(
    bbox: BBox, start: str, end: str, cache: diskcache.Cache
) -> tuple[list[EmitPlume], list[str]]:
    """Cached combined plume list + the source paths queried (~1 day TTL)."""
    key = cache_key("emit_plumes", bbox=roi_key_part(bbox), start=str(start), end=str(end))
    cached = cache.get(key)
    if cached is not None:
        plumes = [_out_to_plume(EmitPlumeOut(**p)) for p in cached["plumes"]]
        return plumes, list(cached["paths"])

    plumes, paths = _compute_plumes(bbox, start, end)
    cache.set(
        key,
        {"plumes": [_plume_to_out(p).model_dump() for p in plumes], "paths": paths},
        expire=_EMIT_TTL_SECONDS,
    )
    return plumes, paths


def list_plumes_out(bbox: BBox, start: str, end: str, cache: diskcache.Cache) -> EmitPlumesOut:
    """Route helper: the plume list as the wire schema."""
    plumes, paths = list_plumes(bbox, start, end, cache)
    return EmitPlumesOut(
        plumes=[_plume_to_out(p) for p in plumes],
        provenance_paths=paths,  # type: ignore[arg-type]
    )


# ── Detection cross-match ──


def match_detection(engine: Engine, cache: diskcache.Cache, det_id: str) -> DetectionDetailOut:
    """Cross-match a detection against EMIT plumes near it in space/time; persist emit_json."""
    with Session(engine) as session:
        row = _require_detection(session, det_id)
        det_lat, det_lon = _detection_center(row)
        det_time = datetime.fromisoformat(row.scene_time_utc)

    bbox = BBox(
        det_lon - _MATCH_HALF_DEG,
        det_lat - _MATCH_HALF_DEG,
        det_lon + _MATCH_HALF_DEG,
        det_lat + _MATCH_HALF_DEG,
    )
    query_start = (det_time - timedelta(days=_MATCH_QUERY_DAYS)).date().isoformat()
    query_end = (det_time + timedelta(days=_MATCH_QUERY_DAYS)).date().isoformat()
    plumes, paths = list_plumes(bbox, query_start, query_end, cache)

    matches = cross_match(
        det_lat, det_lon, det_time, plumes, max_km=_MATCH_MAX_KM, max_days=_MATCH_MAX_DAYS
    )
    result = EmitMatchResult(
        checked_at=utcnow_iso(),
        provenance_paths=paths,  # type: ignore[arg-type]
        matches=[
            EmitMatchOut(
                plume=_plume_to_out(m.plume),
                distance_km=round(m.distance_km, 3),
                dt_hours=round(m.dt_days * 24.0, 2),
            )
            for m in matches
        ],
    )

    with Session(engine) as session:
        row = _require_detection(session, det_id)
        row.emit_json = result.model_dump_json()
        row.updated_at = utcnow_iso()
        session.add(row)
        session.commit()

    return get_detection_detail(engine, det_id)
