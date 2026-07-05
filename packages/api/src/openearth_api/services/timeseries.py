"""Timeseries jobs: submit a chunked series job, serve its cached result.

The core engine is imported by name so offline tests fake it at this module
level (see ``packages/api/tests/test_timeseries.py``). The heavy result lives
as parquet **bytes inside the one diskcache tier** — no side directory of
files — keyed by the same canonical key as the tiles/thumbnail cache, so an
identical request replays instantly and inherits the LRU/TTL policy.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import TYPE_CHECKING, Any

import pandas as pd
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response

from openearth.errors import validate_date_range
from openearth.timeseries import daily_timeseries
from openearth_api.cache import cache_key, roi_key_part, ttl_for
from openearth_api.schemas import (
    JobCreated,
    TimeseriesPoint,
    TimeseriesRequest,
    TimeseriesResultOut,
)
from openearth_api.services.tiles import resolve_catalog

if TYPE_CHECKING:
    import diskcache

    from openearth.catalog.models import DatasetSpec, ProductSpec
    from openearth.geometry import ROI
    from openearth_api.jobs import JobContext, JobManager

# A coarse pass reduces at 4× the native pixel size for a fast preview.
COARSE_SCALE_FACTOR = 4

_PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"


def _effective_scale_m(dataset: DatasetSpec, scale: str) -> int:
    return dataset.default_scale_m * (COARSE_SCALE_FACTOR if scale == "coarse" else 1)


def _frame_to_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"date": ts.strftime("%Y-%m-%d"), "value": float(value), "count": int(count)}
        for ts, value, count in zip(frame.index, frame["value"], frame["count"], strict=True)
    ]


def _resolve(req: TimeseriesRequest) -> tuple[DatasetSpec, ProductSpec, ROI, int, str]:
    """Validate the request at submit time (422/404 here, never inside a job)."""
    dataset, product = resolve_catalog(req.dataset, req.product)  # 404/422 builder
    if product.is_rgb:
        raise HTTPException(
            status_code=422,
            detail=f"Timeseries needs a scalar product; {product.key!r} is an RGB composite.",
        )
    validate_date_range(req.dates.start, req.dates.end)  # → 422
    roi = req.roi.to_domain()  # → 422 on malformed geometry
    scale_m = _effective_scale_m(dataset, req.scale)
    key = cache_key(
        "timeseries",
        dataset=req.dataset,
        product=req.product,
        roi=roi_key_part(roi),
        dates={"start": req.dates.start.isoformat(), "end": req.dates.end.isoformat()},
        scale_m=scale_m,
    )
    return dataset, product, roi, scale_m, key


def _make_runner(
    req: TimeseriesRequest,
    roi: ROI,
    product: ProductSpec,
    scale_m: int,
    key: str,
    cache: diskcache.Cache,
) -> Any:
    """Build the job runner: cache-hit replay, or compute → cache → return."""

    def runner(ctx: JobContext) -> dict[str, Any]:
        cached = cache.get(key)
        if cached is not None:
            frame = pd.read_parquet(BytesIO(cached))
            # Uniform client flow: emit the whole series as one preview event.
            ctx.publish("points", {"points": _frame_to_points(frame)})
            return {"cache_key": key, "cached": True}

        def on_chunk(done: int, total: int, chunk_frame: pd.DataFrame) -> None:
            ctx.progress(done, total, _chunk_message(done, total, chunk_frame))
            if not chunk_frame.empty:
                ctx.publish("points", {"points": _frame_to_points(chunk_frame)})

        frame = daily_timeseries(
            req.product,
            req.dataset,
            roi,
            req.dates.start,
            req.dates.end,
            scale_m=scale_m,
            on_chunk=on_chunk,
            cancel=ctx.cancelled,
        )
        buffer = BytesIO()
        frame.to_parquet(buffer)
        cache.set(key, buffer.getvalue(), expire=ttl_for(req.dates.end))
        return {"cache_key": key}

    return runner


def _chunk_message(done: int, total: int, chunk_frame: pd.DataFrame) -> str:
    if chunk_frame.empty:
        return f"chunk {done}/{total}"
    span = f"{chunk_frame.index.min():%Y-%m-%d}…{chunk_frame.index.max():%Y-%m-%d}"
    return f"chunk {done}/{total} ({span})"


async def submit_timeseries(
    req: TimeseriesRequest, jobs: JobManager, cache: diskcache.Cache
) -> JobCreated:
    _dataset, product, roi, scale_m, key = _resolve(req)
    params = {
        "dataset": req.dataset,
        "product": req.product,
        "scale": req.scale,
        "scale_m": scale_m,
        "band": product.band,
        "unit": product.display_unit,
        "display_scale": product.display_scale,
        "dates": {"start": req.dates.start.isoformat(), "end": req.dates.end.isoformat()},
        "cache_key": key,
    }
    runner = _make_runner(req, roi, product, scale_m, key, cache)
    job_id = await jobs.submit("timeseries", params, runner)
    return JobCreated(job_id=job_id)


def timeseries_result(job_id: str, fmt: str, jobs: JobManager, cache: diskcache.Cache) -> Response:
    row = jobs.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")
    if row.status != "succeeded":
        raise HTTPException(
            status_code=409,
            detail=f"Series job is not finished (status={row.status!r}); watch its events.",
        )

    params = _load_params(row.params_json)
    key = params.get("cache_key")
    cached = cache.get(key) if key else None
    if cached is None:
        raise HTTPException(
            status_code=410,
            detail="The cached series was evicted; re-run the series to regenerate it.",
        )

    filename = _download_name(params)
    if fmt == "parquet":
        return Response(
            content=cached,
            media_type=_PARQUET_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}.parquet"'},
        )

    frame = pd.read_parquet(BytesIO(cached))
    if fmt == "csv":
        return Response(
            content=_csv_text(frame, params["unit"]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )

    result = TimeseriesResultOut(
        points=[TimeseriesPoint(**point) for point in _frame_to_points(frame)],
        unit=params["unit"],
        display_scale=params["display_scale"],
        scale_m=params["scale_m"],
        band=params["band"],
    )
    return JSONResponse(content=result.model_dump())


def _load_params(params_json: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(params_json)
    return parsed


def _csv_text(frame: pd.DataFrame, unit: str) -> str:
    lines = [f"# unit: {unit}", "date,value,count"]
    lines += [
        f"{point['date']},{point['value']},{point['count']}" for point in _frame_to_points(frame)
    ]
    return "\n".join(lines) + "\n"


def _download_name(params: dict[str, Any]) -> str:
    dates = params["dates"]
    return f"{params['dataset']}_{params['product']}_{dates['start']}_{dates['end']}"
