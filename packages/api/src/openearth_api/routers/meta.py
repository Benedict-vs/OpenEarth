"""Health and configuration endpoints."""

from __future__ import annotations

from typing import Annotated

import diskcache
from fastapi import APIRouter, Depends, Request

from openearth.settings import Settings
from openearth_api import __version__
from openearth_api.deps import get_app_settings, get_cache
from openearth_api.schemas import CacheStatsOut, ConfigOut, HealthOut

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> HealthOut:
    return HealthOut(status="ok", version=__version__)


@router.get("/config")
def config(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    cache: Annotated[diskcache.Cache, Depends(get_cache)],
) -> ConfigOut:
    return ConfigOut(
        version=__version__,
        ee_project=settings.ee_project,
        ee_initialized=request.app.state.ee_initialized,
        ee_error=request.app.state.ee_error,
        tile_ttl_seconds=settings.tile_ttl_seconds,
        data_dir=str(settings.data_dir),
        cache=CacheStatsOut(count=len(cache), volume_bytes=cache.volume()),
    )
