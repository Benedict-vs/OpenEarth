"""Catalog lookups over the built-in datasets.

User-defined datasets (TOML loader → "any public GEE collection, zero code
changes") land here in Phase 1; the lookup API is already dataset-keyed so
that addition is purely additive.
"""

from __future__ import annotations

from openearth.catalog.builtin import S1_DATASET, S2_DATASET, S5P_DATASET
from openearth.catalog.models import DatasetSpec, ProductSpec

DATASETS: dict[str, DatasetSpec] = {
    S5P_DATASET.id: S5P_DATASET,
    S2_DATASET.id: S2_DATASET,
    S1_DATASET.id: S1_DATASET,
}

# The v1 "methane" mode is a virtual source that routes per product key.
_S1_KEYS = frozenset(S1_DATASET.products)


def get_dataset(dataset_id: str) -> DatasetSpec:
    try:
        return DATASETS[dataset_id]
    except KeyError:
        valid = ", ".join(sorted(DATASETS))
        raise KeyError(f"Unknown dataset {dataset_id!r}. Valid: {valid}") from None


def get_product(dataset_id: str, key: str) -> ProductSpec:
    return get_dataset(dataset_id).get(key)


def resolve_source(data_key: str, source: str) -> str:
    """Resolve the ``"methane"`` sentinel to a concrete dataset id."""
    if source == "methane":
        if data_key == "CH4":
            return "s5p"
        if data_key in _S1_KEYS:
            return "s1"
        return "s2"
    return source


def resolve_product(data_key: str, source: str) -> tuple[str, ProductSpec]:
    """Return ``(dataset_id, product)`` honoring the ``"methane"`` sentinel."""
    dataset_id = resolve_source(data_key, source)
    return dataset_id, get_product(dataset_id, data_key)
