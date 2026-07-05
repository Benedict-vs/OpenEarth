"""Pixel inspector: sample one point of the current composite.

Reuses the tiles service's catalog resolution and composite builder — the
point value is a single ``Reducer.first()`` reduceRegion at the dataset's
native scale on the very ``ee.Image`` a tile would render. ``sample_point``
is the only Earth-Engine round-trip and is imported by name so offline tests
fake it (see ``packages/api/tests/test_inspect.py``); everything else runs
unfaked.
"""

from __future__ import annotations

import ee
from fastapi import HTTPException

from openearth.ee.client import ee_call
from openearth_api.schemas import InspectRequest, InspectResult, TilesRequest
from openearth_api.services.tiles import GLOBAL_BBOX, build_image, resolve_catalog


def sample_point(image: ee.Image, band: str, lon: float, lat: float, scale_m: int) -> float | None:
    """Reduce *image*'s *band* to the pixel value under (*lon*, *lat*).

    One EE round-trip through :func:`ee_call`. ``Reducer.first()`` at
    *scale_m* returns the single pixel covering the point; a masked pixel
    yields ``None`` (honest "no data", not an error).
    """
    stats = image.select([band]).reduceRegion(
        reducer=ee.Reducer.first(),
        geometry=ee.Geometry.Point([lon, lat]),
        scale=scale_m,
    )
    info = ee_call(stats.getInfo)
    value = (info or {}).get(band)
    return None if value is None else float(value)


def _as_tiles_request(req: InspectRequest) -> TilesRequest:
    """The composite half of the request — ``build_image`` takes a TilesRequest."""
    return TilesRequest(
        dataset=req.dataset,
        product=req.product,
        roi=req.roi,
        composite=req.composite,
        dates=req.dates,
        target_date=req.target_date,
        half_window_days=req.half_window_days,
        timestamp_ms=req.timestamp_ms,
    )


def inspect_point(req: InspectRequest) -> InspectResult:
    """Resolve, build the composite, and sample it at the requested point."""
    dataset, spec = resolve_catalog(req.dataset, req.product)  # 404 / 422 builder
    if spec.is_rgb:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Inspect needs a scalar product; {spec.key!r} is an RGB "
                "composite — pick a scalar product."
            ),
        )
    roi = req.roi.to_domain() if req.roi is not None else GLOBAL_BBOX
    image = build_image(_as_tiles_request(req), roi)  # per-mode 422 like tiles
    value = sample_point(image, spec.band, req.lon, req.lat, dataset.default_scale_m)
    return InspectResult(
        value=value,
        band=spec.band,
        unit=spec.display_unit,
        display_scale=spec.display_scale,
    )
