"""FastAPI dependencies: app-scoped settings, cache, and lazy EE init."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from openearth.ee.client import initialize

if TYPE_CHECKING:
    import diskcache

    from openearth.settings import Settings


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_cache(request: Request) -> diskcache.Cache:
    cache: diskcache.Cache = request.app.state.cache
    return cache


def ensure_ee(request: Request) -> None:
    """Initialize Earth Engine once, lazily; 503 with a fix-it hint otherwise.

    The lifespan already attempts init at startup when a project is
    configured; this retries on demand so the user can authenticate in a
    terminal and use the running server without restarting it.
    """
    state = request.app.state
    if state.ee_initialized:
        return
    try:
        initialize(state.settings.ee_project)
    except Exception as exc:
        state.ee_error = str(exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Earth Engine is not initialized: "
                f"{exc} — set OPENEARTH_EE_PROJECT and run `earthengine authenticate`."
            ),
        ) from exc
    state.ee_initialized = True
    state.ee_error = None
