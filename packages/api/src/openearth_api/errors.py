"""Exception → HTTP mapping over the core error taxonomy."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import ee
from fastapi.responses import JSONResponse

from openearth.errors import (
    EmptyCollectionError,
    InvalidDatasetSpecError,
    InvalidDateRangeError,
    InvalidROIError,
    OpenEarthError,
    classify_ee_error,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)

# classify_ee_error categories → HTTP status. Auth failures are server-side
# configuration (the API's EE credentials), hence 503 rather than 401.
_EE_CATEGORY_STATUS = {
    "auth": 503,
    "quota": 429,
    "timeout": 504,
    "empty": 404,
    "unknown": 502,
}


def _detail(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": message})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidROIError)
    @app.exception_handler(InvalidDateRangeError)
    @app.exception_handler(InvalidDatasetSpecError)
    async def _invalid_input(request: Request, exc: OpenEarthError) -> JSONResponse:
        return _detail(422, str(exc))

    @app.exception_handler(EmptyCollectionError)
    async def _empty(request: Request, exc: EmptyCollectionError) -> JSONResponse:
        return _detail(404, str(exc))

    @app.exception_handler(ee.EEException)
    async def _ee_error(request: Request, exc: ee.EEException) -> JSONResponse:
        category, message = classify_ee_error(exc)
        logger.warning("Earth Engine error (%s): %s", category, exc)
        return _detail(_EE_CATEGORY_STATUS[category], message)

    @app.exception_handler(OpenEarthError)
    async def _openearth_error(request: Request, exc: OpenEarthError) -> JSONResponse:
        logger.error("Unhandled OpenEarth error: %s", exc)
        return _detail(500, str(exc))
