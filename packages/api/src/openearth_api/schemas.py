"""Request/response models. Domain validation stays in core.

ROI models are a thin, discriminated JSON shape over the frozen core
dataclasses; ``to_domain()`` defers to ``BBox``/``PolygonROI`` construction
so ``InvalidROIError`` (mapped to 422) remains the single source of truth
for geometric validation.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

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
    needs_ref: bool = False  # two-window compare product (pre + post) → show a ref picker


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
    # Data-adaptive vis range: compute the scale from the composite's own
    # percentiles (``compute_vis_range``) instead of the catalog default. Ignored
    # for RGB products and when an explicit ``viz_overrides`` range is supplied.
    auto_range: bool = False
    # Reference window that unlocks the CH4_ANOMALY quicklook (a builder product
    # that 422s without it): the target is the composite's ``target_date``.
    methane_ref: DateRangeIn | None = None
    # Reference window for two-window compare products (``needs_ref``, e.g. DNBR /
    # FLOOD_VV_CHANGE): pre = ``ref``, post = ``dates``. Kept distinct from
    # ``methane_ref`` (a later cleanup can unify them). 422 when absent.
    ref: DateRangeIn | None = None


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
    """The Explore view's time state. Phase 8 (``v: 2``) is the window/period
    model: a **window** (``center`` ± ``half_window_days``) and a **period**
    (``period_start``/``period_end``). The v1 fields (``mode``/``start``/``end``/
    ``target_date``) are kept optional so old snapshots still validate and the
    client migrates them on load. The server validates shape, never semantics."""

    # v2 — window + period
    center: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    # ``half_window_days`` spans both versions (v1 date-window half-width, v2
    # window half-width). Widened to the window custom bound (0–183 d).
    half_window_days: int = Field(ge=0, le=183)
    # v1 — kept optional for migration
    mode: Literal["range", "single"] | None = None
    start: date | None = None
    end: date | None = None
    target_date: date | None = None


class WorkspaceState(BaseModel):
    """A restorable snapshot of the Explore view. ``v`` is a schema version so
    the shape can migrate explicitly instead of being guessed at load time; an
    unknown version fails validation rather than being silently misread. v1
    snapshots still load (the client migrates them to the window/period model)."""

    v: Literal[1, 2]
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
    """``site_id`` and/or ``roi`` locate the analysis — with both, ``roi`` is
    the analysis bbox and the detection stays linked to the site (site ROIs
    are browse-scale and exceed the 20 m chip limit). ``seed`` makes the
    Monte Carlo reproducible; a re-run with the same seed is bit-for-bit."""

    site_id: int | None = None
    roi: BBoxIn | None = None
    target_scene_id: str = Field(min_length=1)
    reference_scene_id: str | None = None
    # Opt-in composite reference (MBMP only): a median over up to 5 same-orbit
    # scenes, robust against an intermittent plume contaminating the background.
    reference_mode: Literal["single", "composite"] = "single"
    method: Literal["mbmp", "mbsp"] = "mbmp"
    k_sigma: float = Field(default=2.0, ge=0.5, le=5.0)
    min_area_px: int = Field(default=5, ge=1, le=100000)
    source_lonlat: tuple[float, float] | None = None
    seed: int = 0

    @model_validator(mode="after")
    def _composite_excludes_explicit_reference(self) -> AnalyzeRequest:
        # An explicit reference scene IS single mode — the two are contradictory.
        if self.reference_mode == "composite" and self.reference_scene_id is not None:
            raise ValueError(
                "reference_mode='composite' cannot be combined with an explicit "
                "reference_scene_id (pick one: a chosen scene is single-reference mode)."
            )
        return self


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
    score: float | None = None  # ML candidate score (max prob); None for physics rows
    # EMIT plume matches: count when a cross-match has run, None = never checked.
    emit_matches: int | None = None
    # Read-time ML↔physics agreement (fix 8), derived live from physics rows on
    # the same site+scene: agree (physics found a plume) / physics_no_plume
    # (physics ran, empty) / physics_not_run. None for physics rows.
    physics_agreement: Literal["agree", "physics_no_plume", "physics_not_run"] | None = None
    # Empirical noise-floor context (fix 1 + fix 9b), derived at read time from the
    # packaged noise_floor_v1.json. floor_source: "site" (own site) | "global"
    # (unknown/custom) | None (floor not frozen). below_noise_floor: q_kg_h ≤ floor —
    # indistinguishable from this pipeline's retrieval noise. Physics AND ML rows.
    noise_floor_kg_h: float | None = None
    floor_source: Literal["site", "global"] | None = None
    below_noise_floor: bool = False
    flags: list[str]
    created_at: str
    updated_at: str


class DetectionDetailOut(DetectionOut):
    """Full detail: numbers, params, mask + overlay geometry, validation, EMIT."""

    reference_scene_id: str | None
    ime_kg: float | None
    notes: str | None
    result: dict[str, Any]
    params: dict[str, Any]
    mask_geojson: dict[str, Any] | None
    # Map image-source corners, [[w,n],[e,n],[e,s],[w,s]] (EPSG:4326).
    overlay_bounds: list[list[float]] | None
    validation: dict[str, Any] | None
    emit_json: EmitMatchResult | None = None  # EMIT cross-match evidence (migration 5)


class DetectionPatch(BaseModel):
    status: Literal["candidate", "accepted", "rejected"] | None = None
    notes: str | None = None


class DetectionCreated(BaseModel):
    detection_id: str


class ScreeningRequest(BaseModel):
    """S5P Tier-1 screening over a bbox. ``top_n`` hotspots fit the job result."""

    roi: BBoxIn
    start: date
    end: date
    background_days: int = Field(default=30, ge=1, le=365)
    cell_deg: float = Field(default=0.05, gt=0.0, le=1.0)
    sigma_thresh: float = Field(default=2.0, ge=0.0)
    top_n: int = Field(default=50, ge=1, le=500)


class HotspotOut(BaseModel):
    lat: float
    lon: float
    mean_enh_ppb: float
    max_enh_ppb: float
    score: float
    weeks_flagged: int
    weeks_observed: int


class MlScanRequest(BaseModel):
    """Scan a site's S2 scenes over a date range with the ONNX U-Net ranker.

    ``roi``, when given, is the analysis bbox (site ROIs are browse-scale and
    exceed the 20 m chip limit); hits stay linked to ``site_id`` either way."""

    site_id: int
    roi: BBoxIn | None = None
    start: date
    end: date
    max_scenes: int | None = Field(default=None, ge=1, le=200)


class MlStatusOut(BaseModel):
    """ML model availability for the Settings page (never raises when absent)."""

    model_config = ConfigDict(protected_namespaces=())  # allow model_* field names

    model_loaded: bool
    model_version: str | None = None
    latency_ms_p50: float | None = None


class ReferenceEventOut(BaseModel):
    id: int
    source: str
    event_time_utc: str
    lat: float
    lon: float
    q_kg_h: float | None
    q_sigma_kg_h: float | None
    imported_at: str


class NoiseFloorOut(BaseModel):
    """Per-site noise-floor context for the Lab panel (static, before a run)."""

    floor_kg_h: float | None
    floor_source: Literal["site", "global"] | None
    detect_rate: float | None  # share of plume-free pairs that "detected" (site only)
    n_pairs: int | None


class ValidationImportOut(BaseModel):
    imported: int
    skipped: int
    # Imported events whose rate was present in the source but not stored: an
    # ambiguous unit (unit-agnostic column under unit="auto") or the >500 t/h
    # sanity guard. The events still import and cross-match (space + time).
    rates_dropped: int = 0


class ValidationOut(BaseModel):
    verdict: Literal["confirmed", "plausible", "unvalidated", "contradicted"]
    matched_event_ids: list[int]


# ── EMIT plumes (Phase 6) ────────────────────────────────────


class EmitPlumeOut(BaseModel):
    """One EMIT methane plume complex. ``provenance`` distinguishes the source."""

    plume_id: str
    outline: dict[str, Any]  # GeoJSON geometry (Polygon/MultiPolygon, EPSG:4326)
    time_utc: str
    provenance: Literal["gee_v001", "lpdaac_v002"]
    max_enh_ppm_m: float | None
    max_enh_lat: float | None
    max_enh_lon: float | None
    q_kg_h: float | None  # emission rate — V002 only; null for the frozen GEE mirror
    q_sigma_kg_h: float | None
    source_scenes: list[str]


class EmitPlumesOut(BaseModel):
    """Plume list plus which source paths were queried (the GEE freeze is honest)."""

    plumes: list[EmitPlumeOut]
    provenance_paths: list[Literal["gee_v001", "lpdaac_v002"]]


class EmitMatchOut(BaseModel):
    plume: EmitPlumeOut
    distance_km: float
    dt_hours: float  # signed: plume time − detection scene time


class EmitMatchResult(BaseModel):
    """Stored on a detection (``emit_json``): the outcome of a cross-match run."""

    checked_at: str
    provenance_paths: list[Literal["gee_v001", "lpdaac_v002"]]
    matches: list[EmitMatchOut]


# ── Embeddings Explorer (AlphaEarth, Phase 6) ────────────────


class EmbeddingSimilarityRequest(BaseModel):
    """Cosine-similarity layer to the embedding at a clicked seed point."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    year: int
    roi: BBoxIn | None = None  # optional client viewport echo; the tile is global


class EmbeddingChangeRequest(BaseModel):
    """Year-to-year embedding change (1 − cosine) layer."""

    year_a: int
    year_b: int
    roi: BBoxIn | None = None


class EmbeddingClusterRequest(BaseModel):
    """Unsupervised k-means over the embedding within an ROI (required — it trains there)."""

    roi: BBoxIn
    year: int
    k: int = Field(default=6)  # clamped to [2, 12] server-side


class EmbeddingTileOut(BaseModel):
    tile_url: str
    expires_at: datetime
    attribution: str
    legend: LegendOut
    seed_norm: float | None = None  # similarity: ‖seed‖ sanity echo (≈ 1.0)
    n_clusters: int | None = None  # cluster: k actually used after clamping


class EmbeddingYearsOut(BaseModel):
    years: list[int]


# ── Timelapse ────────────────────────────────────────────────


class StepIn(BaseModel):
    """How the date range is sliced into frames (mirrors core ``frame_windows``)."""

    mode: Literal["interval", "monthly", "quarterly"] = "interval"
    interval_days: int = Field(default=16, ge=1, le=366)
    window_days: int | None = Field(default=None, ge=1, le=366)


class AnnotationsIn(BaseModel):
    date_label: bool = True
    colorbar: bool = True
    scale_bar: bool = True
    attribution: str | None = None


class GradeIn(BaseModel):
    """A colour grade (decision 5): a declared tone curve + composable sliders."""

    curve: Literal["natural", "vivid", "cinematic"] = "natural"
    brightness: float = Field(default=0.0, ge=-1.0, le=1.0)
    contrast: float = Field(default=0.0, ge=-1.0, le=1.0)
    saturation: float = Field(default=1.0, ge=0.0, le=2.0)


class ExtrasIn(BaseModel):
    """Share extras, re-encoded from the kept frames (never a re-render)."""

    title_card: str | None = Field(default=None, max_length=120)
    end_card: str | None = Field(default=None, max_length=120)
    watermark: str | None = Field(default=None, max_length=60)
    crops: list[Literal["1:1", "9:16"]] = Field(default_factory=list)

    @field_validator("crops")
    @classmethod
    def _dedupe_crops(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))  # keep order, drop duplicates


# "composite" (show the composited/gap-filled pixel), "raw" (leave holes
# transparent), or "tint:#RRGGBB" (Survey — paint remaining holes).
_CLOUD_DISPLAY_RE = re.compile(r"^(composite|raw|tint:#[0-9a-fA-F]{6})$")


class TimelapseRequest(BaseModel):
    """A timelapse render request. ``roi`` is required — no global timelapse.

    Every Phase-10 field defaults to the legacy behaviour (mean composite, no
    post-processing, no fallback, 1080 longest edge), so an old client renders
    byte-equivalent output.
    """

    title: str | None = Field(default=None, max_length=200)
    dataset: str
    product: str
    roi: RoiIn
    dates: DateRangeIn
    step: StepIn = Field(default_factory=StepIn)
    fps: int = Field(default=6, ge=1, le=30)
    # Duration-first authoring (decision 8): when set, the fps is derived from the
    # frame count to hit ~this many seconds. Mutually exclusive with an explicit fps.
    duration_s: float | None = Field(default=None, gt=0.0, le=120.0)
    format: Literal["mp4", "gif", "webm"] = "mp4"
    # Cap raised 1920 → 3840 after the Stage 0 4K spike. Upscaling past the ROI's
    # native GSD is allowed (decision-9 reversal): honesty is the manifest's
    # native_max_dim readout, not a server-side clamp.
    max_dim: int = Field(default=1080, ge=64, le=3840)
    tween: int = Field(default=0, ge=0, le=4)
    annotations: AnnotationsIn = Field(default_factory=AnnotationsIn)
    vis_min: float | None = None
    vis_max: float | None = None
    # ── Phase 10 production knobs ──
    preset: str | None = Field(default=None, max_length=60)
    composite: Literal["mean", "median", "clearest"] = "mean"
    cloud_display: str = "composite"
    gap_fill: bool = False
    deflicker: bool = False
    grade: GradeIn | None = None
    fallback_source: bool = False
    draft: bool = False
    extras: ExtrasIn = Field(default_factory=ExtrasIn)

    @field_validator("cloud_display")
    @classmethod
    def _valid_cloud_display(cls, value: str) -> str:
        if not _CLOUD_DISPLAY_RE.match(value):
            raise ValueError('cloud_display must be "composite", "raw", or "tint:#RRGGBB".')
        return value

    @model_validator(mode="after")
    def _duration_xor_fps(self) -> TimelapseRequest:
        if self.duration_s is not None and "fps" in self.model_fields_set:
            raise ValueError(
                "Set either fps (frame-first) or duration_s (duration-first), not both."
            )
        return self


class PreflightRequest(BaseModel):
    """A cheap availability probe (decision 11) — collection aggregates only."""

    dataset: str
    product: str
    roi: RoiIn
    dates: DateRangeIn
    step: StepIn = Field(default_factory=StepIn)
    composite: Literal["mean", "median", "clearest"] = "mean"
    fallback_source: bool = False


class PreflightWindowOut(BaseModel):
    start: date
    end: date
    label: str
    scene_count: int
    mean_cloud: float | None = None  # scene-level cloud %, where the source reports it
    source: str  # which ladder source supplied the count (primary or fallback)


class PreflightOut(BaseModel):
    """Per-window availability + the native-resolution ceiling for the ROI."""

    windows: list[PreflightWindowOut]
    frame_count: int  # windows with ≥1 scene somewhere on the ladder
    empty_count: int
    native_max_dim: int  # the sensor limit readout — the UI labels upscaled renders with it


class TimelapseCreated(BaseModel):
    job_id: str
    render_id: str


class RenderUpdateIn(BaseModel):
    """Editable render metadata — currently just the gallery title."""

    title: str = Field(min_length=1, max_length=200)


class RenderOut(BaseModel):
    """Gallery row: SQL row + a couple of fields surfaced from ``params_json``."""

    id: str
    title: str
    dataset: str
    product: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    frame_count: int | None
    fps: int
    format: Literal["mp4", "gif", "webm"]
    movie_bytes: int | None
    created_at: str
    updated_at: str
    # Phase 10 — read from params_json (no migration): draft chip + preset name +
    # the crop variants that were also encoded (for the gallery download menu).
    draft: bool = False
    preset: str | None = None
    crops: list[str] = Field(default_factory=list)


class RenderDetailOut(RenderOut):
    """A render row plus its parsed ``manifest.json`` (``None`` until finished)."""

    roi: RoiIn
    params: dict[str, Any]
    manifest: dict[str, Any] | None


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
