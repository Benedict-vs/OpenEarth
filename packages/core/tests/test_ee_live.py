"""Live Earth Engine integration tests.

Run explicitly with real per-user auth (never in CI):

    OPENEARTH_EE_TESTS=1 uv run pytest -m ee
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.ee,
    pytest.mark.skipif(
        os.environ.get("OPENEARTH_EE_TESTS") != "1",
        reason="live EE tests need OPENEARTH_EE_TESTS=1 and real auth",
    ),
]

HEIDELBERG_DATES = ("2024-06-01", "2024-07-01")


@pytest.fixture(scope="module", autouse=True)
def _ee_session() -> None:
    from openearth.ee.client import initialize

    initialize()


def test_tile_mint_s2_ndvi_over_heidelberg() -> None:
    from openearth.catalog import get_product
    from openearth.catalog.presets import ROI_PRESETS
    from openearth.composites import build_mean_composite
    from openearth.ee.render import mint_tile_url

    roi = ROI_PRESETS["Heidelberg (Germany)"].bbox
    image = build_mean_composite("NDVI", roi, *HEIDELBERG_DATES, source="s2")
    ref = mint_tile_url(image, get_product("s2", "NDVI"))
    assert ref.url.startswith("https://")
    assert "{z}" in ref.url


def test_s5p_no2_collection_nonempty() -> None:
    from openearth.catalog.presets import ROI_PRESETS
    from openearth.ee.client import ee_call
    from openearth.providers.s5p import get_trace_gas_collection

    roi = ROI_PRESETS["Heidelberg (Germany)"].bbox
    col = get_trace_gas_collection("NO2", roi, *HEIDELBERG_DATES)
    assert ee_call(col.size().getInfo) > 0


def test_overpass_matched_wind_sample() -> None:
    from datetime import UTC, datetime

    from openearth.catalog.presets import METHANE_SITES
    from openearth.methane.wind import sample_wind_at

    roi = METHANE_SITES["CH4: Korpezhe, Turkmenistan"].bbox
    sample = sample_wind_at(roi, datetime(2024, 7, 15, 7, 24, tzinfo=UTC))
    assert sample.speed_ms >= 0
    assert 0 <= sample.wind_from_deg < 360
