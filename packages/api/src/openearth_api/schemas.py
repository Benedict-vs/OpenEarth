"""Request/response models. Domain validation stays in core.

ROI models are a thin, discriminated JSON shape over the frozen core
dataclasses; ``to_domain()`` defers to ``BBox``/``PolygonROI`` construction
so ``InvalidROIError`` (mapped to 422) remains the single source of truth
for geometric validation.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

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


class SceneOut(BaseModel):
    timestamp_ms: int
    datetime_utc: datetime


# ── Presets ──────────────────────────────────────────────────


class RoiPresetOut(BaseModel):
    name: str
    category: Literal["continent", "city", "methane_site"]
    bbox: BBoxIn
    date_hint: tuple[date, date] | None = None


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
