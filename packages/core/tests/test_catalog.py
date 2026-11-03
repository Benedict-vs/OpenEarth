"""Catalog integrity: every ported entry is well-formed and the v1 defect
(CH4_ANOMALY's vestigial expression) stays fixed."""

from __future__ import annotations

import re
from datetime import date

import pytest

from openearth.catalog import DATASETS, get_product, resolve_product
from openearth.catalog.builtin.s2 import (
    METHANE_S2_KEYS,
    S2_COLLECTION_ID,
    S2_L1C_AVAILABLE_FROM,
    S2_SR_AVAILABLE_FROM,
    S2_SR_COLLECTION_ID,
)
from openearth.catalog.models import DatasetSpec, ProductSpec
from openearth.catalog.registry import resolve_source

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

ALL_PRODUCTS = [
    pytest.param(ds_id, key, id=f"{ds_id}/{key}")
    for ds_id, ds in DATASETS.items()
    for key in ds.products
]


def test_expected_datasets_and_counts() -> None:
    assert set(DATASETS) == {"s5p", "s2", "s1", "emit", "hls", "landsat"}
    assert len(DATASETS["s5p"].products) == 6  # NO2, SO2, CO, O3, CH4, HCHO
    assert len(DATASETS["s1"].products) == 5  # VV, VH, VV_VH_RATIO, RVI, FLOOD_VV_CHANGE
    assert len(DATASETS["s2"].products) >= 30  # indices + raw bands + RGB + proxies
    assert len(DATASETS["emit"].products) == 1  # CH4ENH
    # Phase 10 optical deep-history sources: canonical RGB/NDVI/NDWI on each.
    assert set(DATASETS["hls"].products) == {"RGB", "NDVI", "NDWI"}
    assert set(DATASETS["landsat"].products) == {"RGB", "NDVI", "NDWI"}


def test_emit_ch4enh_registry_round_trip() -> None:
    ds = DATASETS["emit"]
    assert ds.collection_id == "NASA/EMIT/L2B/CH4ENH"
    assert ds.default_scale_m == 60
    assert "EMIT" in ds.title
    spec = get_product("emit", "CH4ENH")
    # Raw-band product: the generic pipeline selects this band, no expression.
    assert spec.band == "vertical_column_enhancement"
    assert spec.source_band == "vertical_column_enhancement"
    assert spec.expression is None
    assert spec.builder is None  # flows through the generic pipeline
    assert spec.display_unit == "ppm·m"
    # Generously negative valid_min for the symmetric matched-filter noise.
    assert spec.valid_min < 0 < spec.vis_min or spec.vis_min == 0.0
    assert spec.valid_min <= spec.vis_min
    assert spec.vis_max <= spec.valid_max


@pytest.mark.parametrize(("ds_id", "key"), ALL_PRODUCTS)
def test_product_well_formed(ds_id: str, key: str) -> None:
    spec = get_product(ds_id, key)
    assert spec.key == key
    assert spec.name
    assert spec.vis_min < spec.vis_max
    assert spec.valid_min < spec.valid_max
    # vis range must sit inside the physically valid range
    assert spec.valid_min <= spec.vis_min
    assert spec.vis_max <= spec.valid_max
    assert spec.display_scale > 0
    for color in spec.palette:
        assert _HEX.match(color), f"{ds_id}/{key}: bad palette color {color!r}"
    if spec.expression is not None:
        assert spec.bands, f"{ds_id}/{key}: expression without input bands"


def test_compare_recipes_declared() -> None:
    dnbr = get_product("s2", "DNBR")
    assert dnbr.needs_ref is True
    assert dnbr.bands == ["B8A", "B12"]
    assert "pre_B8A" in dnbr.expression
    assert "post_B12" in dnbr.expression
    urban = get_product("s2", "URBAN_HEAT")
    assert urban.needs_ref is False  # single-window
    assert urban.expression is not None
    flood = get_product("s1", "FLOOD_VV_CHANGE")
    assert flood.needs_ref is True
    assert flood.expression == "post_VV - pre_VV"


def test_ch4_anomaly_vestigial_expression_stays_dead() -> None:
    spec = get_product("s2", "CH4_ANOMALY")
    assert spec.expression is None, "CH4_ANOMALY must not render through the generic path"
    assert spec.builder == "methane_anomaly"


def test_methane_proxies_pinned_to_toa() -> None:
    assert set(METHANE_S2_KEYS) == {"MBSP", "B12_B11", "CH4_ANOMALY"}
    for key in METHANE_S2_KEYS:
        assert get_product("s2", key).collection_id == S2_COLLECTION_ID


def test_generic_s2_products_default_to_surface_reflectance() -> None:
    assert DATASETS["s2"].collection_id == S2_SR_COLLECTION_ID
    for key in ("NDVI", "NDWI", "EVI", "B11", "B12"):
        spec = get_product("s2", key)
        assert spec.collection_id in (None, S2_SR_COLLECTION_ID)


def test_s5p_source_bands() -> None:
    assert get_product("s5p", "NO2").band == "tropospheric_NO2_column_number_density"
    assert get_product("s5p", "CH4").band == "CH4_column_volume_mixing_ratio_dry_air_bias_corrected"
    # every S5P product names its own L3 collection
    for spec in DATASETS["s5p"].products.values():
        assert spec.collection_id is not None
        assert spec.collection_id.startswith("COPERNICUS/S5P/OFFL/L3_")


def test_s1_honest_naming() -> None:
    assert "VV − VH" in get_product("s1", "VV_VH_RATIO").name
    assert get_product("s1", "VV_VH_RATIO").display_unit == "dB"


def test_methane_sentinel_routing() -> None:
    assert resolve_source("CH4", "methane") == "s5p"
    assert resolve_source("VV", "methane") == "s1"
    assert resolve_source("RVI", "methane") == "s1"
    assert resolve_source("MBSP", "methane") == "s2"
    assert resolve_source("NDVI", "s2") == "s2"
    assert resolve_product("CH4", "methane")[1].display_unit == "ppb"


def test_unknown_lookups_raise_with_valid_keys() -> None:
    with pytest.raises(KeyError, match="NDVI"):
        get_product("s2", "NOPE")
    with pytest.raises(KeyError, match="s5p"):
        get_product("nope", "NDVI")


# ── Temporal coverage (UIs clamp date pickers to it) ─────────────────────


@pytest.mark.parametrize(("ds_id", "key"), ALL_PRODUCTS)
def test_every_builtin_product_has_a_start(ds_id: str, key: str) -> None:
    start, end = DATASETS[ds_id].coverage(key)
    assert start is not None
    assert start >= date(1984, 1, 1)  # Landsat 5 is the oldest archive we serve
    assert end is None or end > start


def test_s5p_gases_narrow_the_dataset_start() -> None:
    s5p = DATASETS["s5p"]
    assert s5p.coverage("NO2") == (date(2018, 6, 28), None)
    assert s5p.coverage("CH4") == (date(2019, 2, 8), None)
    # The dataset bound is the earliest gas, so it never hides a valid product date.
    assert s5p.available_from == min(s5p.coverage(k)[0] for k in s5p.products)


def test_s2_l1c_proxies_reach_back_before_l2a() -> None:
    s2 = DATASETS["s2"]
    assert s2.coverage("NDVI")[0] == S2_SR_AVAILABLE_FROM
    for key in METHANE_S2_KEYS:
        if s2.get(key).collection_id == S2_COLLECTION_ID:
            assert s2.coverage(key)[0] == S2_L1C_AVAILABLE_FROM


def test_emit_mirror_is_closed_ended() -> None:
    assert DATASETS["emit"].coverage("CH4ENH") == (date(2022, 8, 10), date(2024, 11, 30))


def test_inverted_coverage_is_rejected() -> None:
    product = ProductSpec(
        key="X", name="x", vis_min=0, vis_max=1, valid_min=0, valid_max=1, display_unit=""
    )
    with pytest.raises(ValueError, match="ends"):
        DatasetSpec(
            id="bad",
            title="bad",
            collection_id="A/B",
            attribution="",
            default_scale_m=10,
            products={"X": product},
            available_from=date(2020, 1, 1),
            available_to=date(2019, 1, 1),
        )
