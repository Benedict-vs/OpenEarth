"""Unified dataset catalog: datasets → products → visualization specs."""

from openearth.catalog.models import DatasetSpec, ProductSpec
from openearth.catalog.registry import (
    DATASETS,
    get_dataset,
    get_product,
    resolve_product,
)

__all__ = [
    "DATASETS",
    "DatasetSpec",
    "ProductSpec",
    "get_dataset",
    "get_product",
    "resolve_product",
]
