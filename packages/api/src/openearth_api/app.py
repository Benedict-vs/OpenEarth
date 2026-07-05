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
from openearth_api.db import create_db_engine, migrate
from openearth_api.errors import register_exception_handlers
from openearth_api.jobs import JobManager
from openearth_api.routers import catalog, jobs, meta, presets, scenes, tiles, timeseries

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

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.cache = make_cache(settings)

    # DB + job manager come up before EE: they are environment-independent and
    # the interrupted-sweep must run against a migrated schema. Any rows left
    # active by a prior process are marked ``interrupted`` here.
    engine = create_db_engine(settings.data_dir / "openearth.db")
    migrate(engine)
    app.state.db_engine = engine
    app.state.jobs = JobManager(engine)
    app.state.jobs.start()

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
        # Teardown in reverse: drain jobs, dispose the engine, close the cache.
        await app.state.jobs.stop()
        engine.dispose()
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
    app.include_router(tiles.router, prefix="/api")
    app.include_router(scenes.router, prefix="/api")
    app.include_router(presets.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(timeseries.router, prefix="/api")
    return app
