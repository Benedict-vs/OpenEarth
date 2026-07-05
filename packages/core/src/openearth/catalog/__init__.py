"""Unified dataset catalog: datasets → products → visualization specs."""

from openearth.catalog.loader import load_catalog_dir, parse_dataset_toml
from openearth.catalog.models import DatasetSpec, ProductSpec
from openearth.catalog.registry import (
    DATASETS,
    all_datasets,
    clear_user_datasets,
    get_dataset,
    get_product,
    register_dataset,
    resolve_product,
    unregister_dataset,
)

__all__ = [
    "DATASETS",
    "DatasetSpec",
    "ProductSpec",
    "all_datasets",
    "clear_user_datasets",
    "get_dataset",
    "get_product",
    "load_catalog_dir",
    "parse_dataset_toml",
    "register_dataset",
    "resolve_product",
    "unregister_dataset",
]
