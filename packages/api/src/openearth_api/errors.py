"""Exception → HTTP mapping over the core error taxonomy."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import ee
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats (JSON has no NaN/Infinity) with their repr."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default handler echoes each error's ``input``; a NaN/Infinity
        # there (``{"k_sigma": NaN}``) made the 422 itself unserialisable → 500.
        return JSONResponse(
            status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))}
        )

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
