"""Scene discovery: acquisition timestamps for a dataset/product/ROI/dates.

POST (not GET) because polygon ROIs don't fit in query strings. Feeds the
``single_scene`` composite mode; the scene-picker UI arrives in Phase 2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from openearth.errors import validate_date_range
from openearth.providers import list_acquisition_times
from openearth_api.deps import ensure_ee
from openearth_api.schemas import SceneOut, ScenesRequest
from openearth_api.services.tiles import GLOBAL_BBOX, resolve_catalog

router = APIRouter(tags=["scenes"], dependencies=[Depends(ensure_ee)])


@router.post("/scenes")
def list_scenes(body: ScenesRequest) -> list[SceneOut]:
    resolve_catalog(body.dataset, body.product)  # 404 unknown ids, 422 builder products
    validate_date_range(body.dates.start, body.dates.end)
    roi = body.roi.to_domain() if body.roi is not None else GLOBAL_BBOX
    times = list_acquisition_times(
        body.product, roi, body.dates.start, body.dates.end, source=body.dataset
    )
    return [SceneOut(timestamp_ms=int(t.timestamp() * 1000), datetime_utc=t) for t in times]
