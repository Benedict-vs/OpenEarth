"""Generic provider: offline-testable routing and refusal logic.

The EE collection build itself is exercised by the live tests; here we
verify everything that runs before/around Earth Engine: builder refusal,
per-image product computation routing, and the dispatcher sending
user-registered dataset ids down the generic path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import openearth.providers as providers
from openearth.catalog import clear_user_datasets, parse_dataset_toml, register_dataset
from openearth.catalog.models import ProductSpec
from openearth.geometry import BBox
from openearth.providers.generic import _compute_product, get_generic_collection

DEM_TOML = """
[dataset]
id = "dem"
title = "Copernicus DEM GLO-30"
collection_id = "COPERNICUS/DEM/GLO30"
attribution = "ESA"
default_scale_m = 30

[products.DEM]
name = "Elevation"
vis_min = 0
vis_max = 4000
valid_min = -500
valid_max = 9000
display_unit = "m"
"""


@pytest.fixture(autouse=True)
def _isolated_user_registry() -> None:
    clear_user_datasets()
    yield
    clear_user_datasets()


@dataclass
class FakeImage:
    """Duck-typed stand-in recording the EE calls _compute_product makes."""

    calls: list[tuple[str, Any]] = field(default_factory=list)

    def select(self, bands: Any) -> FakeImage:
        self.calls.append(("select", bands))
        return self

    def expression(self, expr: str, band_map: dict[str, Any]) -> FakeImage:
        self.calls.append(("expression", (expr, sorted(band_map))))
        return self

    def rename(self, name: str) -> FakeImage:
        self.calls.append(("rename", name))
        return self

    def copyProperties(self, source: Any, properties: list[str]) -> FakeImage:
        self.calls.append(("copyProperties", tuple(properties)))
        return self


def _spec(**overrides: Any) -> ProductSpec:
    fields: dict[str, Any] = {
        "key": "X",
        "name": "X",
        "vis_min": 0.0,
        "vis_max": 1.0,
        "valid_min": 0.0,
        "valid_max": 1.0,
        "display_unit": "u",
    }
    fields.update(overrides)
    return ProductSpec(**fields)


def test_raw_band_product_selects_source_band() -> None:
    image = FakeImage()
    _compute_product(image, _spec(source_band="LST_Day_1km"))  # type: ignore[arg-type]
    assert image.calls == [("select", "LST_Day_1km")]


def test_expression_product_maps_bands_and_renames() -> None:
    image = FakeImage()
    _compute_product(image, _spec(bands=["B1", "B2"], expression="B1 / B2"))  # type: ignore[arg-type]
    kinds = [name for name, _ in image.calls]
    assert kinds[:1] == ["select"] or kinds[0] == "select"
    assert ("expression", ("B1 / B2", ["B1", "B2"])) in image.calls
    assert ("rename", "X") in image.calls
    assert ("copyProperties", ("system:time_start",)) in image.calls


def test_rgb_product_selects_band_stack() -> None:
    image = FakeImage()
    _compute_product(image, _spec(is_rgb=True, bands=["R", "G", "B"]))  # type: ignore[arg-type]
    assert image.calls == [("select", ["R", "G", "B"])]


def test_builder_product_refused_before_any_ee_work() -> None:
    with pytest.raises(ValueError, match="dedicated builder"):
        _compute_product(FakeImage(), _spec(builder="methane_anomaly"))  # type: ignore[arg-type]
    # And at collection level, using the real builtin CH4_ANOMALY entry:
    roi = BBox(8.5, 49.3, 8.8, 49.5)
    with pytest.raises(ValueError, match="methane_anomaly"):
        get_generic_collection("s2", "CH4_ANOMALY", roi, "2024-01-01", "2024-02-01")


def test_dispatcher_routes_user_datasets_to_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_dataset(parse_dataset_toml(DEM_TOML))
    roi = BBox(8.5, 49.3, 8.8, 49.5)
    seen: dict[str, Any] = {}

    def fake_generic(dataset_id: str, product_key: str, *args: Any, **kwargs: Any) -> str:
        seen["route"] = (dataset_id, product_key)
        return "sentinel-collection"

    monkeypatch.setattr(providers, "get_generic_collection", fake_generic)
    result = providers.get_collection("DEM", roi, "2024-01-01", "2024-02-01", source="dem")
    assert result == "sentinel-collection"
    assert seen["route"] == ("dem", "DEM")


def test_dispatcher_routes_builtin_emit_to_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # emit is a *builtin* dataset but not special-cased in the dispatcher, so
    # CH4ENH must flow through the generic pipeline like a user dataset — the
    # Stage 0 claim that one raw band needs zero provider code.
    roi = BBox(-101.9, 32.38, -101.75, 32.49)
    seen: dict[str, Any] = {}

    def fake_generic(dataset_id: str, product_key: str, *args: Any, **kwargs: Any) -> str:
        seen["route"] = (dataset_id, product_key)
        return "emit-collection"

    monkeypatch.setattr(providers, "get_generic_collection", fake_generic)
    result = providers.get_collection("CH4ENH", roi, "2023-06-01", "2023-07-01", source="emit")
    assert result == "emit-collection"
    assert seen["route"] == ("emit", "CH4ENH")


def test_emit_ch4enh_computes_as_raw_band() -> None:
    from openearth.catalog import get_product

    image = FakeImage()
    _compute_product(image, get_product("emit", "CH4ENH"))  # type: ignore[arg-type]
    assert image.calls == [("select", "vertical_column_enhancement")]
