"""Pixel inspector endpoint: point value of the current composite (EE required)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from openearth_api.deps import ensure_ee
from openearth_api.schemas import InspectRequest, InspectResult
from openearth_api.services.inspect import inspect_point

router = APIRouter(tags=["inspect"], dependencies=[Depends(ensure_ee)])


@router.post("/inspect")
def inspect_route(body: InspectRequest) -> InspectResult:
    return inspect_point(body)
