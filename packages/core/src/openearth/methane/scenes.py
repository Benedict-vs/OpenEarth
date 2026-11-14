"""Sentinel-2 L1C scene catalog for methane retrieval.

Earth Engine is used only to *browse* metadata: one ``ee_call`` per search
maps each image to a null-geometry ``ee.Feature`` carrying just the properties
we need, then ``getInfo``s the FeatureCollection (never the ImageCollection —
its band metadata bloats the payload). Parsing, sorting and reference
selection are pure Python, unit-tested offline.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.builtin.s2 import S2_COLLECTION_ID
from openearth.ee.client import ee_call
from openearth.errors import RetrievalError

if TYPE_CHECKING:
    from openearth.geometry import ROI

# Scene metadata we pull for every candidate. A missing zenith property must
# fail loudly (it would otherwise NaN-poison the AMF), so both are required.
_REQUIRED_PROPS = ("sun_zenith", "view_zenith", "time", "spacecraft")


@dataclass(frozen=True)
class S2Scene:
    """One Sentinel-2 L1C acquisition's metadata (no pixels)."""

    scene_id: str  # system:index
    time: datetime  # UTC, from system:time_start
    cloud_pct: float  # CLOUDY_PIXEL_PERCENTAGE
    relative_orbit: int  # SENSING_ORBIT_NUMBER
    spacecraft: str  # SPACECRAFT_NAME: 'Sentinel-2A' | 'Sentinel-2B'
    sun_zenith_deg: float  # MEAN_SOLAR_ZENITH_ANGLE
    view_zenith_deg: float  # MEAN_INCIDENCE_ZENITH_ANGLE_B12

    @property
    def amf(self) -> float:
        """Two-way air mass factor 1/cos(θ_sun) + 1/cos(θ_view)."""
        return 1.0 / math.cos(math.radians(self.sun_zenith_deg)) + 1.0 / math.cos(
            math.radians(self.view_zenith_deg)
        )


def _image_to_feature(image: ee.Image) -> ee.Feature:
    return ee.Feature(
        None,
        {
            "scene_id": image.get("system:index"),
            "time": image.get("system:time_start"),
            "cloud_pct": image.get("CLOUDY_PIXEL_PERCENTAGE"),
            "relative_orbit": image.get("SENSING_ORBIT_NUMBER"),
            "spacecraft": image.get("SPACECRAFT_NAME"),
            "sun_zenith": image.get("MEAN_SOLAR_ZENITH_ANGLE"),
            "view_zenith": image.get("MEAN_INCIDENCE_ZENITH_ANGLE_B12"),
        },
    )


def _scene_from_props(props: dict[str, Any]) -> S2Scene:
    for key in _REQUIRED_PROPS:
        if props.get(key) is None:
            raise RetrievalError(
                f"Sentinel-2 scene {props.get('scene_id')!r} is missing required "
                f"metadata {key!r}; cannot compute its air mass factor."
            )
    cloud = props.get("cloud_pct")
    return S2Scene(
        scene_id=str(props["scene_id"]),
        time=datetime.fromtimestamp(int(props["time"]) / 1000, tz=UTC),
        cloud_pct=float(cloud) if cloud is not None else math.nan,
        relative_orbit=int(props["relative_orbit"]),
        spacecraft=str(props["spacecraft"]),
        sun_zenith_deg=float(props["sun_zenith"]),
        view_zenith_deg=float(props["view_zenith"]),
    )


def _fetch_scene_features(
    roi: ROI,
    start: str | date | datetime,
    end: str | date | datetime,
    max_cloud: float,
) -> list[dict[str, Any]]:
    """One ``ee_call``: filtered L1C collection → list of per-scene property dicts.

    Isolated so offline tests fake this seam (building the EE graph needs a live
    session); the pure parsing in :func:`_scene_from_props` is what they exercise.
    """
    collection = (
        ee.ImageCollection(S2_COLLECTION_ID)
        .filterBounds(roi.to_ee_geometry())
        .filterDate(to_ee_date(start), to_ee_date(end))
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
    )
    features = ee.FeatureCollection(collection.map(_image_to_feature))
    info = ee_call(features.getInfo) or {}
    return list(info.get("features", []))


def list_scenes(
    roi: ROI,
    start: str | date | datetime,
    end: str | date | datetime,
    *,
    max_cloud: float = 80.0,
) -> list[S2Scene]:
    """List S2 L1C scenes over *roi* in ``[start, end)``, sorted by time."""
    features = _fetch_scene_features(roi, start, end, max_cloud)
    scenes = [_scene_from_props(f["properties"]) for f in features]
    return sorted(scenes, key=lambda s: s.time)


_MGRS_TILE_RE = re.compile(r"_T(\d{2}[A-Z]{3})")


def mgrs_tile(scene_id: str) -> str | None:
    """The MGRS tile id embedded in an S2 system:index, or None (fix 4b)."""
    match = _MGRS_TILE_RE.search(scene_id)
    return match.group(1) if match else None


def dedupe_overpasses(candidates: list[S2Scene], *, prefer_tile: str | None) -> list[S2Scene]:
    """Keep ONE product per acquisition. Pure and unit-tested.

    Near a UTM-zone edge an ROI touches several MGRS tiles, and the catalog lists
    the same acquisition once per tile (``…_T39SYC`` and ``…_T40SBH``, identical
    datatake, timestamps < 1 s apart). Left in, those copies made the composite
    a median over duplicated overpasses (a plume on 2 dates filled 4 of 5
    members) and let the single picker prefer a cross-tile copy on a sub-second
    Δt tie. The overpass key is (spacecraft, datatake sensing time — the first
    ``system:index`` token); within a group the target's own tile wins, then the
    lower cloud cover, then the id (determinism).
    """

    def rank(scene: S2Scene) -> tuple[bool, float, str]:
        cloud = scene.cloud_pct if not math.isnan(scene.cloud_pct) else math.inf
        return (mgrs_tile(scene.scene_id) != prefer_tile, cloud, scene.scene_id)

    best: dict[tuple[str, str], S2Scene] = {}
    for scene in candidates:
        key = (scene.spacecraft, scene.scene_id.split("_", 1)[0])
        if key not in best or rank(scene) < rank(best[key]):
            best[key] = scene
    kept = set(map(id, best.values()))
    return [s for s in candidates if id(s) in kept]


def pick_reference(
    target: S2Scene,
    candidates: list[S2Scene],
    *,
    max_cloud: float = 30.0,
    max_days: int = 120,
    min_days: float = 1.0,
) -> S2Scene | None:
    """Choose the best plume-free reference scene for *target* (MBMP), or None.

    Pure and unit-tested. Excludes the target itself, requires
    ``cloud_pct ≤ max_cloud`` and ``min_days ≤ |Δt| ≤ max_days``, and scores by
    temporal distance plus penalties for a different relative orbit (+30,
    viewing geometry) or spacecraft (+5, SRF); the lowest score wins.

    The ``min_days`` floor excludes a *same-overpass* scene (an adjacent UTM tile
    of the same acquisition, ``Δt ≈ 0``): it images the very same plume, so
    differencing against it would cancel the signal instead of the background.
    Tile copies of one acquisition collapse to a single candidate, preferring
    the target's tile (:func:`dedupe_overpasses`).
    """

    def score(candidate: S2Scene) -> float:
        delta_days = abs((candidate.time - target.time).total_seconds()) / 86400.0
        orbit_penalty = 0.0 if candidate.relative_orbit == target.relative_orbit else 30.0
        sat_penalty = 0.0 if candidate.spacecraft == target.spacecraft else 5.0
        return delta_days + orbit_penalty + sat_penalty

    eligible = [
        c
        for c in dedupe_overpasses(candidates, prefer_tile=mgrs_tile(target.scene_id))
        if c.scene_id != target.scene_id
        and c.cloud_pct <= max_cloud
        and min_days <= abs((c.time - target.time).total_seconds()) / 86400.0 <= max_days
    ]
    if not eligible:
        return None
    return min(eligible, key=score)


def pick_reference_set(
    target: S2Scene,
    candidates: list[S2Scene],
    k: int,
    *,
    max_cloud: float = 30.0,
    max_days: int = 120,
    min_days: float = 1.0,
) -> list[S2Scene]:
    """Choose up to *k* reference scenes for a **median composite** (Phase 8),
    nearest in time first. Pure and unit-tested.

    Unlike :func:`pick_reference`, the relative orbit **and** spacecraft are HARD
    constraints, not soft penalties: the LUT is per-spacecraft and the median is
    only meaningful over a fixed viewing geometry — averaging across mixed
    geometries/SRFs would smear physics, not noise. Same cloud / ``|Δt|`` bounds
    as the single picker; ``min_days`` still excludes the same overpass. Each
    acquisition counts once (:func:`dedupe_overpasses`), so k members are k
    distinct overpasses — the median's contamination bound depends on it.
    Returns ``[]`` when nothing qualifies (the caller falls back to single reference).
    """
    eligible = [
        c
        for c in dedupe_overpasses(candidates, prefer_tile=mgrs_tile(target.scene_id))
        if c.scene_id != target.scene_id
        and c.spacecraft == target.spacecraft
        and c.relative_orbit == target.relative_orbit
        and c.cloud_pct <= max_cloud
        and min_days <= abs((c.time - target.time).total_seconds()) / 86400.0 <= max_days
    ]
    eligible.sort(key=lambda c: abs((c.time - target.time).total_seconds()))
    return eligible[:k]
