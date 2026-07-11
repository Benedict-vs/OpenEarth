"""Tile minting: request → composite ee.Image → XYZ tile URL + legend.

Core functions are imported by name — the monkeypatch seam for offline
tests. Everything before/after the EE calls (catalog resolution, per-mode
validation, ROI conversion, legend shaping) runs unfaked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from openearth.catalog import get_dataset
from openearth.composites import (
    build_date_composite,
    build_mean_composite,
    build_methane_anomaly_composite,
    build_single_scene,
)
from openearth.ee.render import compute_vis_range, mint_tile_url
from openearth.errors import validate_date_range
from openearth.geometry import BBox
from openearth.providers import get_compare_image
from openearth_api.schemas import TileResponse, TilesRequest, VizOverrides
from openearth_api.services.legend import legend_for

if TYPE_CHECKING:
    import ee

    from openearth.catalog.models import DatasetSpec, ProductSpec
    from openearth.geometry import ROI

GLOBAL_BBOX = BBox(-180.0, -90.0, 180.0, 90.0)


def resolve_catalog(dataset_id: str, product_key: str) -> tuple[DatasetSpec, ProductSpec]:
    """Resolve catalog specs; 404 on unknown ids, 422 on builder products."""
    try:
        dataset = get_dataset(dataset_id)
        spec = dataset.get(product_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc

    if spec.builder is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Product {spec.key!r} requires the dedicated compute path "
                f"{spec.builder!r}, which arrives with the Methane Lab (Phase 3)."
            ),
        )
    return dataset, spec


def resolve_request(req: TilesRequest) -> tuple[DatasetSpec, ProductSpec, ROI]:
    """Resolve catalog specs and the domain ROI for a tiles request.

    Builder products still 422 — except ``methane_anomaly`` when a
    ``methane_ref`` window is supplied (the quicklook unlock).
    """
    try:
        dataset = get_dataset(req.dataset)
        spec = dataset.get(req.product)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc

    if spec.builder is not None and not (
        spec.builder == "methane_anomaly" and req.methane_ref is not None
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Product {spec.key!r} requires the dedicated compute path "
                f"{spec.builder!r}; the CH4 quicklook needs a 'methane_ref' window."
            ),
        )
    roi = req.roi.to_domain() if req.roi is not None else GLOBAL_BBOX
    return dataset, spec, roi


def build_image(req: TilesRequest, roi: ROI, spec: ProductSpec) -> ee.Image:
    """Dispatch the composite mode (or the methane-anomaly builder) to core."""
    if spec.builder == "methane_anomaly":
        if req.methane_ref is None:
            raise HTTPException(
                status_code=422, detail="CH4_ANOMALY requires a 'methane_ref' window."
            )
        if req.target_date is None:
            raise HTTPException(
                status_code=422, detail="CH4_ANOMALY quicklook requires 'target_date'."
            )
        return build_methane_anomaly_composite(
            roi,
            req.target_date,
            req.half_window_days,
            req.methane_ref.start,
            req.methane_ref.end,
        )

    if spec.needs_ref:
        if req.ref is None:
            raise HTTPException(
                status_code=422,
                detail=f"{req.product} is a two-window compare product; it needs a 'ref' window.",
            )
        if req.dates is None:
            raise HTTPException(
                status_code=422,
                detail=f"{req.product} needs the request window 'dates' (the post window).",
            )
        validate_date_range(req.ref.start, req.ref.end)
        validate_date_range(req.dates.start, req.dates.end)
        return get_compare_image(
            req.product,
            roi,
            req.ref.start,
            req.ref.end,
            req.dates.start,
            req.dates.end,
            source=req.dataset,
        )

    if req.composite == "mean":
        if req.dates is None:
            raise HTTPException(status_code=422, detail="composite='mean' requires 'dates'.")
        validate_date_range(req.dates.start, req.dates.end)
        return build_mean_composite(
            req.product, roi, req.dates.start, req.dates.end, source=req.dataset
        )

    if req.composite == "date_window":
        if req.target_date is None:
            raise HTTPException(
                status_code=422, detail="composite='date_window' requires 'target_date'."
            )
        return build_date_composite(
            req.product, roi, req.target_date, req.half_window_days, source=req.dataset
        )

    # single_scene
    if req.timestamp_ms is None:
        raise HTTPException(
            status_code=422, detail="composite='single_scene' requires 'timestamp_ms'."
        )
    return build_single_scene(req.product, roi, req.timestamp_ms, source=req.dataset)


def _has_explicit_range(viz: VizOverrides | None) -> bool:
    return viz is not None and (viz.vis_min is not None or viz.vis_max is not None)


def mint_tiles(req: TilesRequest) -> TileResponse:
    dataset, spec, roi = resolve_request(req)
    image = build_image(req, roi, spec)

    viz = req.viz_overrides
    # Auto-range: derive the scale from the composite's own percentiles unless
    # it is RGB or the caller pinned an explicit range. The computed range flows
    # to both the tile mint and the legend so the UI shows what it rendered.
    if req.auto_range and not spec.is_rgb and not _has_explicit_range(viz):
        vmin, vmax = compute_vis_range(image, spec, roi)
        viz = VizOverrides(vis_min=vmin, vis_max=vmax)

    ref = mint_tile_url(
        image,
        spec,
        attribution=dataset.attribution,
        vis_min=viz.vis_min if viz else None,
        vis_max=viz.vis_max if viz else None,
    )
    return TileResponse(
        tile_url=ref.url,
        expires_at=ref.expires_at,
        attribution=ref.attribution,
        legend=legend_for(spec, viz),
    )
