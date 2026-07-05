"""Catalog lookups over built-in and user-registered datasets.

Built-ins live in the import-time-frozen ``DATASETS`` dict and never change.
User datasets (TOML loader → "any public GEE collection, zero code changes")
sit in a separate additive layer: :func:`register_dataset` /
:func:`unregister_dataset` mutate only that layer, and every lookup falls
through to it. Core never loads the user catalog dir implicitly — the API
lifespan calls :func:`openearth.catalog.loader.load_catalog_dir` explicitly,
keeping core imports side-effect-free.
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

# User-registered datasets (TOML). Single-process mutation only — the API
# is the sole writer.
_USER_DATASETS: dict[str, DatasetSpec] = {}


def register_dataset(spec: DatasetSpec) -> None:
    """Add a user dataset. Raises :class:`ValueError` on an id collision."""
    if spec.id in DATASETS:
        raise ValueError(f"Dataset id {spec.id!r} collides with a built-in dataset.")
    if spec.id in _USER_DATASETS:
        raise ValueError(f"Dataset id {spec.id!r} is already registered.")
    _USER_DATASETS[spec.id] = spec


def unregister_dataset(dataset_id: str) -> None:
    """Remove a user dataset. Built-ins are refused with :class:`ValueError`."""
    if dataset_id in DATASETS:
        raise ValueError(f"Cannot unregister built-in dataset {dataset_id!r}.")
    if dataset_id not in _USER_DATASETS:
        raise KeyError(f"Unknown user dataset {dataset_id!r}.")
    del _USER_DATASETS[dataset_id]


def all_datasets() -> dict[str, DatasetSpec]:
    """Return a fresh merged view: built-ins first, then user datasets."""
    return {**DATASETS, **_USER_DATASETS}


def clear_user_datasets() -> None:
    """Drop every user dataset (test isolation / catalog reload hook)."""
    _USER_DATASETS.clear()


def get_dataset(dataset_id: str) -> DatasetSpec:
    try:
        return DATASETS[dataset_id]
    except KeyError:
        pass
    try:
        return _USER_DATASETS[dataset_id]
    except KeyError:
        valid = ", ".join(sorted({**DATASETS, **_USER_DATASETS}))
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
