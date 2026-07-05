"""Application factory.

``create_app()`` must stay free of Earth Engine work at creation time:
``scripts/export_openapi.py`` (and therefore the web build) instantiates the
app offline to dump its OpenAPI schema. Everything environment-dependent
happens in the lifespan, which only runs when a server actually starts.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openearth.catalog import load_catalog_dir, register_dataset
from openearth.ee.client import initialize
from openearth.settings import Settings, get_settings
from openearth_api import __version__
from openearth_api.cache import make_cache
from openearth_api.errors import register_exception_handlers
from openearth_api.routers import catalog, meta

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    for spec in load_catalog_dir(settings.data_dir / "catalog.d").values():
        try:
            register_dataset(spec)
        except ValueError as exc:
            logger.warning("Skipping user dataset %r: %s", spec.id, exc)

    app.state.cache = make_cache(settings)

    # Non-fatal EE init attempt so /config reports real status; routes that
    # need EE retry lazily via deps.ensure_ee.
    if settings.ee_project:
        try:
            await asyncio.to_thread(initialize, settings.ee_project)
            app.state.ee_initialized = True
        except Exception as exc:
            logger.warning("Earth Engine init failed at startup: %s", exc)
            app.state.ee_error = str(exc)
    else:
        app.state.ee_error = "OPENEARTH_EE_PROJECT is not set."

    try:
        yield
    finally:
        app.state.cache.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. *settings* overrides env-derived settings (tests)."""
    app = FastAPI(title="OpenEarth API", version=__version__, lifespan=_lifespan)
    app.state.settings = settings if settings is not None else get_settings()
    app.state.ee_initialized = False
    app.state.ee_error = None

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(meta.router, prefix="/api")
    app.include_router(catalog.router, prefix="/api")
    return app
