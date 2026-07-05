"""TOML custom-dataset loader — "one new dataset = zero new code".

A user describes any public GEE ImageCollection in a small TOML file and it
becomes a first-class :class:`~openearth.catalog.models.DatasetSpec`, browsable
through the generic provider with no code changes. Validation is strict and
message-first: a typo'd key or missing field must fail loudly at load time,
never at render time inside Earth Engine.

Schema (``[dataset]`` table + one ``[products.KEY]`` table per product)::

    [dataset]
    id = "modis_lst"                  # ^[a-z0-9_]{1,64}$
    title = "MODIS Land Surface Temperature"
    collection_id = "MODIS/061/MOD11A1"
    attribution = "NASA LP DAAC"
    default_scale_m = 1000

    [products.LST_DAY]
    name = "LST (day)"
    source_band = "LST_Day_1km"       # optional; defaults to the product key
    vis_min = 13000.0
    vis_max = 16500.0
    valid_min = 7500.0
    valid_max = 65535.0
    display_unit = "K"
    display_scale = 0.02              # optional; see ProductSpec for the rest

``builder`` and ``methane_only`` are internal escape hatches and are rejected
in user TOML.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path
from typing import Any

from openearth.catalog.models import DatasetSpec, ProductSpec
from openearth.errors import InvalidDatasetSpecError

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_PRODUCT_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_DATASET_REQUIRED = {
    "id": str,
    "title": str,
    "collection_id": str,
    "attribution": str,
    "default_scale_m": int,
}

_PRODUCT_REQUIRED = {
    "name": str,
    "vis_min": float,
    "vis_max": float,
    "valid_min": float,
    "valid_max": float,
    "display_unit": str,
}

_PRODUCT_OPTIONAL = {
    "description": str,
    "display_scale": float,
    "palette": list,
    "bands": list,
    "expression": str,
    "is_rgb": bool,
    "collection_id": str,
    "source_band": str,
}

_PRODUCT_FORBIDDEN = ("builder", "methane_only", "key")


def _typecheck(table: str, key: str, value: Any, expected: type) -> Any:
    """Return *value* coerced to *expected*, or raise with a precise message.

    TOML integers are accepted where floats are expected (``1000`` for
    ``1000.0``), but bools are not (bool is an int subclass in Python).
    """
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if expected is int and isinstance(value, bool):
        raise InvalidDatasetSpecError(f"{table}: {key!r} must be an integer, got a boolean.")
    if not isinstance(value, expected):
        raise InvalidDatasetSpecError(
            f"{table}: {key!r} must be {expected.__name__}, got {type(value).__name__}."
        )
    return value


def _check_keys(table: str, data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise InvalidDatasetSpecError(
            f"{table}: unknown key(s) {', '.join(map(repr, unknown))}. "
            f"Valid keys: {', '.join(sorted(allowed))}."
        )


def _parse_product(key: str, raw: Any) -> ProductSpec:
    table = f"[products.{key}]"
    if not _PRODUCT_KEY_RE.match(key):
        raise InvalidDatasetSpecError(f"Product key {key!r} must match {_PRODUCT_KEY_RE.pattern}.")
    if not isinstance(raw, dict):
        raise InvalidDatasetSpecError(f"{table} must be a table of product fields.")
    for forbidden in _PRODUCT_FORBIDDEN:
        if forbidden in raw:
            raise InvalidDatasetSpecError(
                f"{table}: {forbidden!r} is not allowed in user dataset TOML."
            )
    _check_keys(table, raw, set(_PRODUCT_REQUIRED) | set(_PRODUCT_OPTIONAL))

    fields: dict[str, Any] = {}
    for name, expected in _PRODUCT_REQUIRED.items():
        if name not in raw:
            raise InvalidDatasetSpecError(f"{table}: missing required key {name!r}.")
        fields[name] = _typecheck(table, name, raw[name], expected)
    for name, expected in _PRODUCT_OPTIONAL.items():
        if name in raw:
            fields[name] = _typecheck(table, name, raw[name], expected)

    if fields["vis_max"] <= fields["vis_min"]:
        raise InvalidDatasetSpecError(
            f"{table}: vis_max ({fields['vis_max']}) must be > vis_min ({fields['vis_min']})."
        )
    if fields["valid_max"] < fields["valid_min"]:
        raise InvalidDatasetSpecError(
            f"{table}: valid_max ({fields['valid_max']}) must be >= "
            f"valid_min ({fields['valid_min']})."
        )

    if "palette" in fields:
        palette = [_typecheck(table, "palette entry", c, str) for c in fields["palette"]]
        bad = [c for c in palette if not _HEX_COLOR_RE.match(c)]
        if bad:
            raise InvalidDatasetSpecError(
                f"{table}: palette entries must be '#rrggbb' hex colors; "
                f"got {', '.join(map(repr, bad))}."
            )
        fields["palette"] = palette  # fresh list — never alias caller data

    if "bands" in fields:
        fields["bands"] = [_typecheck(table, "bands entry", b, str) for b in fields["bands"]]
        if not fields["bands"]:
            raise InvalidDatasetSpecError(f"{table}: 'bands' must not be empty.")

    if fields.get("expression") is not None and "bands" not in fields:
        raise InvalidDatasetSpecError(
            f"{table}: 'expression' requires 'bands' listing its input bands."
        )
    if fields.get("is_rgb") and "bands" not in fields:
        raise InvalidDatasetSpecError(
            f"{table}: is_rgb products must list their 'bands' (e.g. red/green/blue)."
        )

    return ProductSpec(key=key, **fields)


def parse_dataset_toml(text: str) -> DatasetSpec:
    """Parse and validate one dataset definition from TOML *text*.

    Raises :class:`InvalidDatasetSpecError` on any syntactic or semantic
    problem, with a message precise enough to fix the file.
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidDatasetSpecError(f"Invalid TOML syntax: {exc}") from exc

    _check_keys("top level", doc, {"dataset", "products"})
    if "dataset" not in doc or not isinstance(doc["dataset"], dict):
        raise InvalidDatasetSpecError("Missing [dataset] table.")
    raw_dataset = doc["dataset"]
    _check_keys("[dataset]", raw_dataset, set(_DATASET_REQUIRED))

    ds_fields: dict[str, Any] = {}
    for name, expected in _DATASET_REQUIRED.items():
        if name not in raw_dataset:
            raise InvalidDatasetSpecError(f"[dataset]: missing required key {name!r}.")
        ds_fields[name] = _typecheck("[dataset]", name, raw_dataset[name], expected)

    if not _ID_RE.match(ds_fields["id"]):
        raise InvalidDatasetSpecError(
            f"[dataset]: id {ds_fields['id']!r} must match {_ID_RE.pattern}."
        )
    if ds_fields["default_scale_m"] <= 0:
        raise InvalidDatasetSpecError("[dataset]: default_scale_m must be positive.")

    raw_products = doc.get("products")
    if not isinstance(raw_products, dict) or not raw_products:
        raise InvalidDatasetSpecError("At least one [products.KEY] table is required.")

    products = {key: _parse_product(key, raw) for key, raw in raw_products.items()}
    return DatasetSpec(products=products, **ds_fields)


def load_catalog_dir(path: Path) -> dict[str, DatasetSpec]:
    """Load every ``*.toml`` dataset definition under *path*.

    Malformed files and duplicate ids are skipped with a logged warning
    rather than failing the whole load — one bad file must not take the
    catalog (or the API startup) down. Strict validation for new files
    happens at write time (``parse_dataset_toml`` via the API).
    """
    datasets: dict[str, DatasetSpec] = {}
    if not path.is_dir():
        return datasets
    for file in sorted(path.glob("*.toml")):
        try:
            spec = parse_dataset_toml(file.read_text(encoding="utf-8"))
        except InvalidDatasetSpecError as exc:
            logger.warning("Skipping invalid dataset file %s: %s", file, exc)
            continue
        if spec.id in datasets:
            logger.warning("Skipping %s: duplicate dataset id %r (already loaded).", file, spec.id)
            continue
        datasets[spec.id] = spec
    return datasets
