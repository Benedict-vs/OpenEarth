"""Tile and thumbnail endpoints (Earth Engine required)."""

from __future__ import annotations

from typing import Annotated

import diskcache
from fastapi import APIRouter, Depends, Response

from openearth_api.deps import ensure_ee, get_cache
from openearth_api.schemas import ThumbnailRequest, TileResponse, TilesRequest
from openearth_api.services.thumbnails import render_thumbnail
from openearth_api.services.tiles import mint_tiles

router = APIRouter(tags=["render"], dependencies=[Depends(ensure_ee)])


@router.post("/tiles")
def mint_tiles_route(body: TilesRequest) -> TileResponse:
    return mint_tiles(body)


@router.post(
    "/thumbnail",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def thumbnail_route(
    body: ThumbnailRequest,
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
) -> Response:
    png = render_thumbnail(body, cache)
    return Response(content=png, media_type="image/png")
