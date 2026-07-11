"""Catalog data model: frozen specs generalizing the v1 per-sensor registries.

``ProductSpec`` is the superset of the old ``GasConfig`` / ``S2IndexConfig`` /
``S1BandConfig`` dataclasses so every v1 entry ports without loss; new fields
(``builder``, ``source_band``) make previously implicit behavior explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# Neutral grey ramp used when an entry doesn't specify a palette.
DEFAULT_PALETTE: list[str] = [
    "#000000",
    "#1c1c1c",
    "#383838",
    "#555555",
    "#717171",
    "#8d8d8d",
    "#aaaaaa",
    "#c6c6c6",
    "#e2e2e2",
    "#ffffff",
]


@dataclass(frozen=True)
class ProductSpec:
    """Immutable descriptor for one displayable/analyzable product.

    Attributes beyond the shared v1 fields:
        bands: input bands consumed by ``expression`` (Sentinel-2 indices).
        expression: EE band-math expression; ``None`` → select raw band(s).
        source_band: raw band name in the collection when it differs from
            ``key`` (e.g. S5P ``NO2`` → ``NO2_column_number_density``).
        collection_id: per-product override of the dataset's collection
            (e.g. S2 methane proxies pin L1C TOA; RGB pins L2A SR).
        builder: name of a dedicated compute path when the generic
            select/expression pipeline CANNOT produce this product
            (e.g. ``"methane_anomaly"``). Providers must refuse to build
            such products generically.
    """

    key: str
    name: str
    vis_min: float
    vis_max: float
    valid_min: float
    valid_max: float
    display_unit: str
    description: str = ""
    display_scale: float = 1.0
    palette: list[str] = field(default_factory=lambda: list(DEFAULT_PALETTE))
    bands: list[str] | None = None
    expression: str | None = None
    is_rgb: bool = False
    collection_id: str | None = None
    methane_only: bool = False
    source_band: str | None = None
    builder: str | None = None
    # Two-window compare recipe: ``expression`` references ``pre_``/``post_``-prefixed
    # bands (from ``bands``), built from a reference and a request window. Refused by
    # the single-window pipeline; rendered via ``get_compare_image``. Unlike
    # ``builder`` this is allowed in user TOML (it needs no bespoke code).
    needs_ref: bool = False

    @property
    def band(self) -> str:
        """Output/selection band name."""
        return self.source_band or self.key


@dataclass(frozen=True)
class DatasetSpec:
    """Immutable descriptor for a data source (one or more EE collections)."""

    id: str
    title: str
    collection_id: str
    attribution: str
    default_scale_m: int
    products: Mapping[str, ProductSpec]

    def __post_init__(self) -> None:
        # Freeze the mapping and check key consistency once at import time.
        object.__setattr__(self, "products", MappingProxyType(dict(self.products)))
        for key, product in self.products.items():
            if key != product.key:
                raise ValueError(
                    f"Dataset {self.id!r}: product dict key {key!r} != {product.key!r}"
                )

    def get(self, key: str) -> ProductSpec:
        try:
            return self.products[key]
        except KeyError:
            valid = ", ".join(sorted(self.products))
            raise KeyError(
                f"Unknown product {key!r} in dataset {self.id!r}. Valid: {valid}"
            ) from None
