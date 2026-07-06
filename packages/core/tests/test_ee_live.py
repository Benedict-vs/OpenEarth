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


def test_daily_timeseries_ndvi_over_heidelberg() -> None:
    from datetime import date

    from openearth.catalog.presets import ROI_PRESETS
    from openearth.timeseries import daily_timeseries

    roi = ROI_PRESETS["Heidelberg (Germany)"].bbox
    # One 30-day chunk; validates the real reduceRegion output-key naming
    # (``value_mean`` / ``value_count``) that offline fakes can't check.
    frame = daily_timeseries("NDVI", "s2", roi, date(2024, 6, 1), date(2024, 7, 1))
    assert list(frame.columns) == ["value", "count"]
    assert not frame.empty  # June over Heidelberg has clear Sentinel-2 scenes
    assert (frame["count"] > 0).all()
    assert frame["value"].between(-1, 1).all()  # NDVI physical range


def test_export_geotiff_ndvi_fast_path(tmp_path: object) -> None:
    from pathlib import Path

    import numpy as np
    import rasterio

    from openearth.catalog import get_product
    from openearth.catalog.presets import ROI_PRESETS
    from openearth.composites import build_mean_composite
    from openearth.export import export_geotiff

    roi = ROI_PRESETS["Heidelberg (Germany)"].bbox
    image = build_mean_composite("NDVI", roi, *HEIDELBERG_DATES, source="s2")
    dest = Path(str(tmp_path)) / "ndvi.tif"
    # A coarse 200 m grid keeps the payload tiny → the getDownloadURL fast path.
    export_geotiff(image, get_product("s2", "NDVI"), roi, 200, dest)

    with rasterio.open(dest) as src:
        assert src.crs.to_epsg() == 4326
        assert src.count == 1
        assert np.isfinite(src.read(1)).any()  # some valid NDVI pixels landed


def test_overpass_matched_wind_sample() -> None:
    from datetime import UTC, datetime

    from openearth.catalog.presets import METHANE_SITES
    from openearth.methane.wind import sample_wind_at

    roi = METHANE_SITES["CH4: Korpezhe, Turkmenistan"].bbox
    sample = sample_wind_at(roi, datetime(2024, 7, 15, 7, 24, tzinfo=UTC))
    assert sample.speed_ms >= 0
    assert 0 <= sample.wind_from_deg < 360


def test_wind_field_over_permian_basin() -> None:
    import math
    from datetime import UTC, datetime

    from openearth.geometry import BBox
    from openearth.methane.wind import GLOBAL_ERA5_HOURLY_ID, sample_wind_field

    # Permian Basin, West Texas: a small all-land box so ERA5-Land is populated
    # in every cell (no open-water masking) and the whole field must be finite.
    bbox = BBox(-103.0, 31.5, -102.0, 32.5)
    field = sample_wind_field(
        bbox,
        datetime(2024, 7, 15, 12, 0, tzinfo=UTC),
        nx=4,
        ny=4,
        fallback_collection_id=GLOBAL_ERA5_HOURLY_ID,
    )
    assert (field.nx, field.ny) == (4, 4)
    assert len(field.u) == 16
    assert len(field.v) == 16
    assert all(math.isfinite(x) for x in field.u)
    assert all(math.isfinite(x) for x in field.v)


def test_list_scenes_korpezhe_june_2018() -> None:
    from openearth.catalog.presets import METHANE_SITES
    from openearth.methane.scenes import list_scenes

    roi = METHANE_SITES["CH4: Korpezhe, Turkmenistan"].bbox
    scenes = list_scenes(roi, "2018-06-01", "2018-07-01", max_cloud=90.0)
    assert scenes, "expected non-empty Korpezhe June 2018 scene list"
    # The documented 2018-06-19 super-emitter acquisition must be present.
    assert any(s.time.date().isoformat() == "2018-06-19" for s in scenes)
    for s in scenes:
        assert s.spacecraft in ("Sentinel-2A", "Sentinel-2B")
        assert s.amf > 1.0
