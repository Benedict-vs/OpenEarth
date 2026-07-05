"""ROI presets (continents, cities, methane sites with date hints)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from openearth.catalog.presets import ROI_PRESETS
from openearth_api.schemas import BBoxIn, RoiPresetOut

router = APIRouter(tags=["presets"])


@router.get("/presets/rois")
def list_roi_presets() -> list[RoiPresetOut]:
    return [
        RoiPresetOut(
            name=preset.name,
            category=preset.category,
            bbox=BBoxIn(
                west=preset.bbox.west,
                south=preset.bbox.south,
                east=preset.bbox.east,
                north=preset.bbox.north,
            ),
            date_hint=(
                (date.fromisoformat(preset.date_hint[0]), date.fromisoformat(preset.date_hint[1]))
                if preset.date_hint
                else None
            ),
        )
        for preset in ROI_PRESETS.values()
    ]
