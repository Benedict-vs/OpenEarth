"""Catalog endpoints: built-in + user datasets, custom-dataset CRUD."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from openearth.catalog import (
    DATASETS,
    all_datasets,
    get_dataset,
    parse_dataset_toml,
    register_dataset,
    unregister_dataset,
)
from openearth.catalog.models import DatasetSpec, ProductSpec
from openearth.settings import Settings
from openearth_api.deps import get_app_settings
from openearth_api.schemas import CustomDatasetIn, DatasetOut, ProductOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog"])


def _product_out(spec: ProductSpec) -> ProductOut:
    return ProductOut(
        key=spec.key,
        name=spec.name,
        display_unit=spec.display_unit,
        vis_min=spec.vis_min,
        vis_max=spec.vis_max,
        valid_min=spec.valid_min,
        valid_max=spec.valid_max,
        display_scale=spec.display_scale,
        palette=list(spec.palette),
        description=spec.description,
        is_rgb=spec.is_rgb,
        methane_only=spec.methane_only,
        requires_builder=spec.builder is not None,
    )


def _dataset_out(spec: DatasetSpec) -> DatasetOut:
    return DatasetOut(
        id=spec.id,
        title=spec.title,
        collection_id=spec.collection_id,
        attribution=spec.attribution,
        default_scale_m=spec.default_scale_m,
        is_custom=spec.id not in DATASETS,
        products=[_product_out(p) for p in spec.products.values()],
    )


@router.get("/catalog")
def list_catalog() -> list[DatasetOut]:
    return [_dataset_out(spec) for spec in all_datasets().values()]


@router.get("/catalog/{dataset_id}")
def get_catalog_dataset(dataset_id: str) -> DatasetOut:
    try:
        spec = get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _dataset_out(spec)


@router.post("/catalog/custom", status_code=status.HTTP_201_CREATED)
def create_custom_dataset(
    body: CustomDatasetIn,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DatasetOut:
    # Raises InvalidDatasetSpecError → 422 via the app-level handler.
    spec = parse_dataset_toml(body.toml)
    if spec.id in all_datasets():
        raise HTTPException(status_code=409, detail=f"Dataset id {spec.id!r} already exists.")

    catalog_dir = settings.data_dir / "catalog.d"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / f"{spec.id}.toml").write_text(body.toml, encoding="utf-8")
    register_dataset(spec)
    logger.info("Registered custom dataset %r.", spec.id)
    return _dataset_out(spec)


@router.delete("/catalog/custom/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_dataset(
    dataset_id: str,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    if dataset_id in DATASETS:
        raise HTTPException(
            status_code=409, detail=f"Dataset {dataset_id!r} is built-in and cannot be deleted."
        )
    try:
        unregister_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    (settings.data_dir / "catalog.d" / f"{dataset_id}.toml").unlink(missing_ok=True)
    logger.info("Deleted custom dataset %r.", dataset_id)
