"""Request/response models. Domain validation stays in core.

ROI models are a thin, discriminated JSON shape over the frozen core
dataclasses; ``to_domain()`` defers to ``BBox``/``PolygonROI`` construction
so ``InvalidROIError`` (mapped to 422) remains the single source of truth
for geometric validation.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from openearth.geometry import BBox, PolygonROI

# ── ROI / dates ──────────────────────────────────────────────


class BBoxIn(BaseModel):
    kind: Literal["bbox"] = "bbox"
    west: float
    south: float
    east: float
    north: float

    def to_domain(self) -> BBox:
        return BBox(self.west, self.south, self.east, self.north)


class PolygonIn(BaseModel):
    kind: Literal["polygon"] = "polygon"
    # Single exterior ring of (lon, lat) pairs, open or closed, no holes.
    coordinates: list[tuple[float, float]]

    def to_domain(self) -> PolygonROI:
        return PolygonROI(tuple(self.coordinates))


RoiIn = Annotated[BBoxIn | PolygonIn, Field(discriminator="kind")]

# Reusable (de)serializer for the discriminated ROI union — used to persist an
# ROI as JSON and read it back (AOIs, workspace layers store it as a blob).
ROI_ADAPTER: TypeAdapter[BBoxIn | PolygonIn] = TypeAdapter(RoiIn)


class DateRangeIn(BaseModel):
    start: date
    end: date


class VizOverrides(BaseModel):
    vis_min: float | None = None
    vis_max: float | None = None


# ── Catalog ──────────────────────────────────────────────────


class ProductOut(BaseModel):
    key: str
    name: str
    display_unit: str
    vis_min: float
    vis_max: float
    valid_min: float
    valid_max: float
    display_scale: float
    palette: list[str]
    description: str
    is_rgb: bool
    methane_only: bool
    requires_builder: bool


class DatasetOut(BaseModel):
    id: str
    title: str
    collection_id: str
    attribution: str
    default_scale_m: int
    is_custom: bool
    products: list[ProductOut]


class CustomDatasetIn(BaseModel):
    toml: str


# ── Tiles / thumbnails / scenes ──────────────────────────────

CompositeMode = Literal["mean", "date_window", "single_scene"]


class TilesRequest(BaseModel):
    """Per-mode requirements (enforced in the service, 422 on violation):
    mean → ``dates``; date_window → ``target_date`` (± ``half_window_days``);
    single_scene → ``timestamp_ms``.
    """

    dataset: str
    product: str
    roi: RoiIn | None = None
    composite: CompositeMode = "mean"
    dates: DateRangeIn | None = None
    target_date: date | None = None
    half_window_days: int = Field(default=3, ge=0, le=30)
    timestamp_ms: int | None = None
    viz_overrides: VizOverrides | None = None
    # Reference window that unlocks the CH4_ANOMALY quicklook (a builder product
    # that 422s without it): the target is the composite's ``target_date``.
    methane_ref: DateRangeIn | None = None


class LegendOut(BaseModel):
    min: float
    max: float
    unit: str
    palette: list[str]
    display_scale: float
    is_rgb: bool
    description: str


class TileResponse(BaseModel):
    tile_url: str
    expires_at: datetime
    attribution: str
    legend: LegendOut


class ThumbnailRequest(TilesRequest):
    width: int = Field(default=1024, ge=64, le=2048)


class ScenesRequest(BaseModel):
    dataset: str
    product: str
    roi: RoiIn | None = None
    dates: DateRangeIn


# ── Export ───────────────────────────────────────────────────


class ExportGeotiffRequest(BaseModel):
    """A GeoTIFF export of the current composite. Composite fields mirror
    ``TilesRequest`` (minus ``viz_overrides`` — raw values, no colour scaling);
    ``scale_m`` overrides the dataset's native metres-per-pixel. ``roi`` is
    required — a global native-resolution export is unbounded compute.
    """

    dataset: str
    product: str
    roi: RoiIn
    composite: CompositeMode = "mean"
    dates: DateRangeIn | None = None
    target_date: date | None = None
    half_window_days: int = Field(default=3, ge=0, le=30)
    timestamp_ms: int | None = None
    scale_m: int | None = Field(default=None, ge=1)


# ── Inspect (pixel value) ────────────────────────────────────


class InspectRequest(BaseModel):
    """One point sample of the current composite. The composite fields mirror
    ``TilesRequest`` (minus ``viz_overrides`` — a single pixel value has no
    colour scaling); ``lon``/``lat`` are the sample point in EPSG:4326 degrees.
    The ``roi`` still governs how the composite is built, not where it is read.
    """

    dataset: str
    product: str
    roi: RoiIn | None = None
    composite: CompositeMode = "mean"
    dates: DateRangeIn | None = None
    target_date: date | None = None
    half_window_days: int = Field(default=3, ge=0, le=30)
    timestamp_ms: int | None = None
    lon: float
    lat: float


class InspectResult(BaseModel):
    """A ``value`` of ``null`` means the pixel is masked (no data at that
    location) — not an error. Multiply by ``display_scale`` for display units."""

    value: float | None
    band: str
    unit: str
    display_scale: float


# ── Timeseries ───────────────────────────────────────────────


class TimeseriesRequest(BaseModel):
    """ROI is required — a global reduceRegion series is unbounded compute
    (plan.md). ``scale`` picks native resolution or the 4× coarse preview."""

    dataset: str
    product: str
    roi: RoiIn
    dates: DateRangeIn
    scale: Literal["coarse", "native"] = "native"


class TimeseriesPoint(BaseModel):
    date: str  # ISO date, e.g. "2019-04-02"
    value: float  # raw reduced value (multiply by display_scale for display)
    count: int  # contributing pixel count that day


class TimeseriesResultOut(BaseModel):
    points: list[TimeseriesPoint]
    unit: str
    display_scale: float
    scale_m: int
    band: str


class SceneOut(BaseModel):
    timestamp_ms: int
    datetime_utc: datetime


# ── Wind ─────────────────────────────────────────────────────


class WindSampleOut(BaseModel):
    """ROI-mean 10 m wind at a single instant (mirrors core ``WindSample``).
    ``wind_from_deg`` is the meteorological convention (direction blown FROM)."""

    when: datetime
    u_ms: float
    v_ms: float
    speed_ms: float
    wind_to_deg: float
    wind_from_deg: float
    collection_id: str


class WindFieldOut(BaseModel):
    """Per-cell mean 10 m wind on an ``nx × ny`` lattice, row-major from the NW
    corner. A masked cell is ``null`` (JSON has no NaN) — the client skips it."""

    when: datetime
    bbox: BBoxIn
    nx: int
    ny: int
    u: list[float | None]
    v: list[float | None]
    collection_id: str


# ── Presets ──────────────────────────────────────────────────


class RoiPresetOut(BaseModel):
    name: str
    category: Literal["continent", "city", "methane_site"]
    bbox: BBoxIn
    date_hint: tuple[date, date] | None = None


# ── Jobs ─────────────────────────────────────────────────────

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]


class JobCreated(BaseModel):
    job_id: str


class JobOut(BaseModel):
    id: str
    kind: str
    status: JobStatus
    progress_done: int
    progress_total: int
    message: str | None
    # Parsed from the row's small ``result_json`` (e.g. ``{"cache_key": …}``).
    result: dict[str, Any] | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


# ── Saved AOIs / workspaces ──────────────────────────────────


class AoiIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    roi: RoiIn


class AoiOut(BaseModel):
    id: int
    name: str
    roi: RoiIn
    created_at: str


class WorkspaceLayer(BaseModel):
    """One layer's persisted shape — data identity plus display state, but no
    mint (tile URLs expire; they are re-minted on load, not restored)."""

    dataset: str
    product: str
    label: str
    opacity: float
    visible: bool
    viz_overrides: VizOverrides | None = None


class WorkspaceDate(BaseModel):
    mode: Literal["range", "single"]
    start: date
    end: date
    target_date: date
    half_window_days: int = Field(ge=0, le=30)


class WorkspaceState(BaseModel):
    """A restorable snapshot of the Explore view. ``v`` is a schema version so
    Phase 3+ can migrate the shape explicitly instead of guessing at load time;
    an unknown version fails validation rather than being silently misread."""

    v: Literal[1]
    layers: list[WorkspaceLayer]
    roi: RoiIn | None = None
    date: WorkspaceDate
    wind: bool


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    state: WorkspaceState


class WorkspaceOut(BaseModel):
    id: int
    name: str
    state: WorkspaceState
    created_at: str
    updated_at: str


# ── Methane Lab (Phase 3) ────────────────────────────────────


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    bbox: BBoxIn
    date_hint_start: date | None = None
    date_hint_end: date | None = None
    notes: str | None = None


class SitePatch(BaseModel):
    """Partial update — only provided fields change (unset fields untouched)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    bbox: BBoxIn | None = None
    date_hint_start: date | None = None
    date_hint_end: date | None = None
    notes: str | None = None


class SiteOut(BaseModel):
    id: int
    name: str
    bbox: BBoxIn
    date_hint_start: date | None
    date_hint_end: date | None
    notes: str | None
    created_at: str


class SceneInfoOut(BaseModel):
    """One S2 scene's metadata for the scene picker. ``ref_ok`` flags a scene
    clear enough (cloud ≤ 30 %) to serve as an MBMP reference."""

    scene_id: str
    time: datetime
    cloud_pct: float
    relative_orbit: int
    spacecraft: str
    sun_zenith_deg: float
    view_zenith_deg: float
    amf: float
    ref_ok: bool


class AnalyzeRequest(BaseModel):
    """Exactly one of ``site_id`` / ``roi`` locates the analysis. ``seed`` makes
    the Monte Carlo reproducible; a re-run with the same seed is bit-for-bit."""

    site_id: int | None = None
    roi: BBoxIn | None = None
    target_scene_id: str = Field(min_length=1)
    reference_scene_id: str | None = None
    method: Literal["mbmp", "mbsp"] = "mbmp"
    k_sigma: float = Field(default=2.0, ge=0.5, le=5.0)
    min_area_px: int = Field(default=5, ge=1, le=100000)
    source_lonlat: tuple[float, float] | None = None
    seed: int = 0


class DetectionOut(BaseModel):
    """Summary row for the detection feed (headline numbers only)."""

    id: str
    site_id: int | None
    source: str
    status: Literal["candidate", "accepted", "rejected"]
    method: str
    scene_id: str
    scene_time_utc: str
    q_kg_h: float | None
    q_sigma_kg_h: float | None
    xch4_max_ppb: float | None
    u10_ms: float | None
    wind_from_deg: float | None
    flags: list[str]
    created_at: str
    updated_at: str


class DetectionDetailOut(DetectionOut):
    """Full detail: numbers, params, mask + overlay geometry, validation."""

    reference_scene_id: str | None
    ime_kg: float | None
    notes: str | None
    result: dict[str, Any]
    params: dict[str, Any]
    mask_geojson: dict[str, Any] | None
    # Map image-source corners, [[w,n],[e,n],[e,s],[w,s]] (EPSG:4326).
    overlay_bounds: list[list[float]] | None
    validation: dict[str, Any] | None


class DetectionPatch(BaseModel):
    status: Literal["candidate", "accepted", "rejected"] | None = None
    notes: str | None = None


class DetectionCreated(BaseModel):
    detection_id: str


# ── Meta ─────────────────────────────────────────────────────


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str


class CacheStatsOut(BaseModel):
    count: int
    volume_bytes: int


class ConfigOut(BaseModel):
    version: str
    ee_project: str | None
    ee_initialized: bool
    ee_error: str | None
    tile_ttl_seconds: int
    data_dir: str
    cache: CacheStatsOut
