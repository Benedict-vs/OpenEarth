"""build_composite mode routing (mean / median / clearest) — offline.

The reducers and qualityMosaic are Earth Engine; here we verify the pure routing:
which reducer each mode selects, that S2 "clearest" delegates to the s2cloudless
qualityMosaic path, that non-S2 "clearest" degenerates to median, and that the
legacy build_mean_composite alias still routes to a plain mean.
"""

from __future__ import annotations

from typing import Any

import pytest

import openearth.composites as composites
from openearth.catalog import get_product
from openearth.composites import build_composite, build_mean_composite
from openearth.geometry import BBox

# Global ROI so _clip_unless_global skips the EE geometry clip entirely.
GLOBAL = BBox(-180.0, -90.0, 180.0, 90.0)


class _FakeReduced:
    def __init__(self, sink: dict[str, Any]) -> None:
        self.sink = sink

    def select(self, band: str) -> _FakeReduced:
        self.sink["selected"] = band
        return self


class _FakeCollection:
    def __init__(self, sink: dict[str, Any]) -> None:
        self.sink = sink

    def mean(self) -> _FakeReduced:
        self.sink["reducer"] = "mean"
        return _FakeReduced(self.sink)

    def median(self) -> _FakeReduced:
        self.sink["reducer"] = "median"
        return _FakeReduced(self.sink)


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    s: dict[str, Any] = {}
    monkeypatch.setattr(composites, "get_collection", lambda *a, **k: _FakeCollection(s))
    # Real (non-RGB) spec so cfg.band == "NDVI" drives the select branch.
    monkeypatch.setattr(
        composites, "get_product_config", lambda *a, **k: get_product("hls", "NDVI")
    )
    return s


def test_mean_mode_reduces_with_mean(sink: dict[str, Any]) -> None:
    build_composite("NDVI", GLOBAL, "2023-01-01", "2023-02-01", source="hls", mode="mean")
    assert sink["reducer"] == "mean"
    assert sink["selected"] == "NDVI"


def test_median_mode_reduces_with_median(sink: dict[str, Any]) -> None:
    build_composite("NDVI", GLOBAL, "2023-01-01", "2023-02-01", source="hls", mode="median")
    assert sink["reducer"] == "median"
    assert sink["selected"] == "NDVI"


def test_legacy_mean_alias_still_uses_mean(sink: dict[str, Any]) -> None:
    build_mean_composite("NDVI", GLOBAL, "2023-01-01", "2023-02-01", source="hls")
    assert sink["reducer"] == "mean"


def test_clearest_non_s2_degenerates_to_median(sink: dict[str, Any]) -> None:
    # HLS/Landsat cloud products are binary → "clearest" == masked median.
    build_composite("NDVI", GLOBAL, "2023-01-01", "2023-02-01", source="hls", mode="clearest")
    assert sink["reducer"] == "median"


def test_clearest_s2_delegates_to_quality_mosaic(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    def fake_clearest(index_key: str, roi: Any, s: Any, e: Any, **kwargs: Any) -> str:
        called["key"] = index_key
        return "quality-mosaic-image"

    monkeypatch.setattr(composites, "get_product_config", lambda *a, **k: get_product("s2", "NDVI"))
    monkeypatch.setattr(composites, "get_s2_clearest_image", fake_clearest)
    # get_collection must NOT be touched on the S2 clearest path.
    monkeypatch.setattr(
        composites,
        "get_collection",
        lambda *a, **k: pytest.fail("clearest S2 must not reduce a collection"),
    )
    result = build_composite(
        "NDVI", GLOBAL, "2023-01-01", "2023-02-01", source="s2", mode="clearest"
    )
    assert result == "quality-mosaic-image"
    assert called["key"] == "NDVI"
