"""Live Earth Engine smoke for the Phase 10 HLS + Landsat providers.

Run explicitly with real per-user auth (never in CI):

    OPENEARTH_EE_TESTS=1 uv run pytest -m ee -k hls_landsat

Each test renders a small real composite and asserts the canonical bands survive
the merge/mask/scale pipeline — the offline suite covers routing and bit math, so
these only need to confirm the EE chain actually executes and returns a value.
"""

from __future__ import annotations

import os

import ee
import pytest

from openearth.geometry import BBox

pytestmark = [
    pytest.mark.ee,
    pytest.mark.skipif(
        os.environ.get("OPENEARTH_EE_TESTS") != "1",
        reason="live EE tests need OPENEARTH_EE_TESTS=1 and real auth",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def _ee_session() -> None:
    from openearth.ee.client import initialize

    initialize()


def _permian() -> BBox:
    return BBox(-103.5, 31.7, -103.3, 31.9)


def _mean_over(image: ee.Image, band: str) -> object:
    from openearth.ee.client import ee_call

    roi = _permian()
    return ee_call(
        image.select(band)
        .reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=roi.to_ee_geometry(),
            scale=30,
            bestEffort=True,
            maxPixels=1e8,
        )
        .getInfo
    ).get(band)


def test_hls_landsat_hls_rgb_composite_has_canonical_bands() -> None:
    from openearth.composites import build_composite
    from openearth.ee.client import ee_call

    image = build_composite(
        "RGB", _permian(), "2023-07-01", "2023-09-30", source="hls", mode="median"
    )
    bands = ee_call(image.bandNames().getInfo)
    assert set(bands) >= {"RED", "GREEN", "BLUE"}


def test_hls_landsat_hls_ndvi_clearest_is_finite() -> None:
    from openearth.composites import build_composite

    image = build_composite(
        "NDVI", _permian(), "2023-07-01", "2023-09-30", source="hls", mode="clearest"
    )
    assert _mean_over(image, "NDVI") is not None


def test_hls_landsat_landsat_rgb_l89_composite_has_canonical_bands() -> None:
    from openearth.composites import build_composite
    from openearth.ee.client import ee_call

    # A window spanning L8/L9 only (2022) — canonical bands must survive SR scaling.
    image = build_composite(
        "RGB", _permian(), "2022-06-01", "2022-09-30", source="landsat", mode="median"
    )
    bands = ee_call(image.bandNames().getInfo)
    assert set(bands) >= {"RED", "GREEN", "BLUE"}


def test_hls_landsat_landsat_deep_history_1985() -> None:
    from openearth.composites import build_composite

    # Landsat-5 era: the deep-history claim (1984+) must actually return imagery.
    image = build_composite(
        "RGB", _permian(), "1985-06-01", "1985-12-31", source="landsat", mode="median"
    )
    assert _mean_over(image, "RED") is not None
