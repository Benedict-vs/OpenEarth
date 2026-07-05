"""TOML dataset loader: validation rules, registry layering, dir loading.

All offline — the loader and registry never touch Earth Engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openearth.catalog import (
    DATASETS,
    all_datasets,
    clear_user_datasets,
    get_dataset,
    get_product,
    load_catalog_dir,
    parse_dataset_toml,
    register_dataset,
    resolve_product,
    unregister_dataset,
)
from openearth.errors import InvalidDatasetSpecError

VALID_TOML = """
[dataset]
id = "modis_lst"
title = "MODIS Land Surface Temperature"
collection_id = "MODIS/061/MOD11A1"
attribution = "NASA LP DAAC"
default_scale_m = 1000

[products.LST_DAY]
name = "LST (day)"
source_band = "LST_Day_1km"
vis_min = 13000.0
vis_max = 16500.0
valid_min = 7500.0
valid_max = 65535.0
display_unit = "K"
display_scale = 0.02
description = "Daytime land surface temperature."
palette = ["#0000ff", "#ffffff", "#ff0000"]
"""

MINIMAL_TOML = """
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


def _replace(toml: str, old: str, new: str) -> str:
    assert old in toml
    return toml.replace(old, new)


# ── parse_dataset_toml ───────────────────────────────────────


def test_valid_toml_round_trips_into_specs() -> None:
    spec = parse_dataset_toml(VALID_TOML)
    assert spec.id == "modis_lst"
    assert spec.collection_id == "MODIS/061/MOD11A1"
    assert spec.default_scale_m == 1000
    product = spec.get("LST_DAY")
    assert product.key == "LST_DAY"
    assert product.band == "LST_Day_1km"  # source_band honored
    assert product.display_scale == 0.02
    assert product.palette == ["#0000ff", "#ffffff", "#ff0000"]
    assert product.builder is None


def test_minimal_toml_uses_defaults() -> None:
    spec = parse_dataset_toml(MINIMAL_TOML)
    product = spec.get("DEM")
    assert product.band == "DEM"  # no source_band → key
    assert product.display_scale == 1.0
    assert product.vis_min == 0.0
    assert isinstance(product.vis_min, float)  # TOML int coerced to float
    assert len(product.palette) == 10  # default grey ramp


def test_expression_product_requires_and_keeps_bands() -> None:
    toml = (
        MINIMAL_TOML
        + """
[products.RATIO]
name = "Band ratio"
bands = ["B1", "B2"]
expression = "B1 / B2"
vis_min = 0.0
vis_max = 2.0
valid_min = 0.0
valid_max = 10.0
display_unit = "ratio"
"""
    )
    product = parse_dataset_toml(toml).get("RATIO")
    assert product.expression == "B1 / B2"
    assert product.bands == ["B1", "B2"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (('id = "dem"', 'id = "Bad-Id!"'), "must match"),
        (("title = ", "titel = "), "unknown key"),
        (("vis_min = 0\n", ""), "missing required key 'vis_min'"),
        (("vis_max = 4000", "vis_max = -10"), "vis_max"),
        (("valid_max = 9000", "valid_max = -600"), "valid_max"),
        (("default_scale_m = 30", "default_scale_m = 0"), "must be positive"),
        (("default_scale_m = 30", 'default_scale_m = "30"'), "must be int"),
        (('display_unit = "m"', "display_unit = 5"), "must be str"),
    ],
)
def test_invalid_specs_rejected_with_precise_message(
    mutation: tuple[str, str], message: str
) -> None:
    toml = _replace(MINIMAL_TOML, *mutation)
    with pytest.raises(InvalidDatasetSpecError, match=message):
        parse_dataset_toml(toml)


def test_invalid_toml_syntax_rejected() -> None:
    with pytest.raises(InvalidDatasetSpecError, match="syntax"):
        parse_dataset_toml("[dataset\nid=")


def test_missing_dataset_table_rejected() -> None:
    with pytest.raises(InvalidDatasetSpecError, match=r"\[dataset\]"):
        parse_dataset_toml("[products.X]\nname = 'x'")


def test_missing_products_rejected() -> None:
    toml = MINIMAL_TOML.split("[products.DEM]")[0]
    with pytest.raises(InvalidDatasetSpecError, match="At least one"):
        parse_dataset_toml(toml)


def test_bad_palette_hex_rejected() -> None:
    toml = _replace(VALID_TOML, '"#ff0000"', '"red"')
    with pytest.raises(InvalidDatasetSpecError, match="hex"):
        parse_dataset_toml(toml)


def test_expression_without_bands_rejected() -> None:
    toml = (
        MINIMAL_TOML
        + "\n[products.R]\nname='r'\nexpression='B1/B2'\n"
        + ("vis_min=0.0\nvis_max=1.0\nvalid_min=0.0\nvalid_max=1.0\ndisplay_unit='x'\n")
    )
    with pytest.raises(InvalidDatasetSpecError, match="requires 'bands'"):
        parse_dataset_toml(toml)


@pytest.mark.parametrize("forbidden", ["builder", "methane_only"])
def test_internal_escape_hatches_forbidden(forbidden: str) -> None:
    toml = MINIMAL_TOML + f"\n[products.X]\nname='x'\n{forbidden} = \"boom\"\n"
    with pytest.raises(InvalidDatasetSpecError, match="not allowed"):
        parse_dataset_toml(toml)


def test_loader_copies_lists_no_aliasing() -> None:
    spec1 = parse_dataset_toml(VALID_TOML)
    spec2 = parse_dataset_toml(VALID_TOML)
    assert spec1.get("LST_DAY").palette is not spec2.get("LST_DAY").palette


# ── registry layering ────────────────────────────────────────


def test_register_and_lookup_user_dataset() -> None:
    spec = parse_dataset_toml(VALID_TOML)
    register_dataset(spec)
    assert get_dataset("modis_lst") is spec
    assert get_product("modis_lst", "LST_DAY").display_unit == "K"
    assert resolve_product("LST_DAY", "modis_lst")[0] == "modis_lst"
    assert "modis_lst" in all_datasets()
    assert "modis_lst" not in DATASETS  # builtin dict untouched


def test_register_collision_with_builtin_refused() -> None:
    spec = parse_dataset_toml(_replace(VALID_TOML, 'id = "modis_lst"', 'id = "s2"'))
    with pytest.raises(ValueError, match="built-in"):
        register_dataset(spec)


def test_register_duplicate_user_id_refused() -> None:
    register_dataset(parse_dataset_toml(VALID_TOML))
    with pytest.raises(ValueError, match="already registered"):
        register_dataset(parse_dataset_toml(VALID_TOML))


def test_unregister_removes_and_refuses_builtins() -> None:
    register_dataset(parse_dataset_toml(VALID_TOML))
    unregister_dataset("modis_lst")
    with pytest.raises(KeyError):
        get_dataset("modis_lst")
    with pytest.raises(ValueError, match="built-in"):
        unregister_dataset("s2")
    with pytest.raises(KeyError):
        unregister_dataset("never_registered")


def test_all_datasets_returns_fresh_dict() -> None:
    view = all_datasets()
    view["hacked"] = view["s2"]
    assert "hacked" not in all_datasets()


def test_unknown_dataset_error_lists_user_ids_too() -> None:
    register_dataset(parse_dataset_toml(VALID_TOML))
    with pytest.raises(KeyError, match="modis_lst"):
        get_dataset("nope")


# ── load_catalog_dir ─────────────────────────────────────────


def test_load_catalog_dir_missing_or_empty(tmp_path: Path) -> None:
    assert load_catalog_dir(tmp_path / "nope") == {}
    assert load_catalog_dir(tmp_path) == {}


def test_load_catalog_dir_loads_valid_files(tmp_path: Path) -> None:
    (tmp_path / "modis_lst.toml").write_text(VALID_TOML)
    (tmp_path / "dem.toml").write_text(MINIMAL_TOML)
    datasets = load_catalog_dir(tmp_path)
    assert set(datasets) == {"modis_lst", "dem"}


def test_load_catalog_dir_skips_malformed_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "good.toml").write_text(MINIMAL_TOML)
    (tmp_path / "bad.toml").write_text("[dataset]\nid = 42\n")
    with caplog.at_level("WARNING"):
        datasets = load_catalog_dir(tmp_path)
    assert set(datasets) == {"dem"}
    assert any("bad.toml" in rec.getMessage() for rec in caplog.records)


def test_load_catalog_dir_skips_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "a.toml").write_text(MINIMAL_TOML)
    (tmp_path / "b.toml").write_text(MINIMAL_TOML)
    datasets = load_catalog_dir(tmp_path)
    assert set(datasets) == {"dem"}
