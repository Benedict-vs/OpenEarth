"""Thumbnail rendering: composite → EE thumb URL → PNG bytes, diskcached.

The EE thumb URL is itself short-lived, so the bytes are fetched server-side
immediately after minting and only the *bytes* are cached — never the URL,
and never an error body.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
from fastapi import HTTPException

from openearth.ee.render import thumb_url
from openearth_api.cache import cache_key, roi_key_part, ttl_for
from openearth_api.services.tiles import build_image, resolve_request

if TYPE_CHECKING:
    from datetime import date

    import diskcache

    from openearth_api.schemas import ThumbnailRequest

_FETCH_TIMEOUT_S = 120


def _fetch_bytes(url: str) -> bytes:
    """Fetch the rendered thumbnail (monkeypatch seam for offline tests)."""
    response = httpx.get(url, timeout=_FETCH_TIMEOUT_S, follow_redirects=True)
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Earth Engine thumbnail fetch failed ({response.status_code}).",
        )
    return response.content


def _effective_end_date(req: ThumbnailRequest) -> date | None:
    """Latest date the request can see — drives the cache TTL policy."""
    if req.composite == "mean" and req.dates is not None:
        return req.dates.end
    if req.composite == "date_window" and req.target_date is not None:
        return req.target_date + timedelta(days=req.half_window_days)
    return None  # single_scene: a fixed past acquisition, immutable


def render_thumbnail(req: ThumbnailRequest, cache: diskcache.Cache) -> bytes:
    _dataset, spec, roi = resolve_request(req)

    key = cache_key(
        "thumbnail",
        dataset=req.dataset,
        product=req.product,
        roi=roi_key_part(req.roi.to_domain() if req.roi else None),
        composite=req.composite,
        dates=[req.dates.start, req.dates.end] if req.dates else None,
        target_date=req.target_date,
        half_window_days=req.half_window_days,
        timestamp_ms=req.timestamp_ms,
        width=req.width,
        viz=req.viz_overrides.model_dump() if req.viz_overrides else None,
    )
    cached = cache.get(key)
    if cached is not None:
        return bytes(cached)

    image = build_image(req, roi)
    viz = req.viz_overrides
    url = thumb_url(
        image,
        spec,
        roi,
        vis_min=viz.vis_min if viz else None,
        vis_max=viz.vis_max if viz else None,
        dimensions=req.width,
    )
    png = _fetch_bytes(url)

    end = _effective_end_date(req)
    cache.set(key, png, expire=None if end is None else ttl_for(end))
    return png
