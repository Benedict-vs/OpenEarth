"""Cache-key stability and TTL policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openearth.geometry import BBox, PolygonROI
from openearth_api import cache
from openearth_api.cache import cache_key, roi_key_part, ttl_for


def test_key_is_deterministic_and_order_independent() -> None:
    a = cache_key("thumbnail", dataset="s2", product="NDVI", width=512)
    b = cache_key("thumbnail", width=512, product="NDVI", dataset="s2")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_key_distinguishes_ops_and_params() -> None:
    base = cache_key("thumbnail", dataset="s2")
    assert cache_key("tiles", dataset="s2") != base
    assert cache_key("thumbnail", dataset="s1") != base


def test_roi_rounding_beyond_5dp_hits_same_key() -> None:
    a = roi_key_part(BBox(8.123456789, 49.1, 8.9, 49.9))
    b = roi_key_part(BBox(8.123459999, 49.1, 8.9, 49.9))
    c = roi_key_part(BBox(8.1236, 49.1, 8.9, 49.9))
    assert a == b
    assert a != c


def test_polygon_and_bbox_with_same_bounds_differ() -> None:
    bbox = BBox(8.0, 49.0, 9.0, 50.0)
    ring = PolygonROI(((8.0, 49.0), (9.0, 49.0), (9.0, 50.0), (8.0, 50.0)))
    assert roi_key_part(bbox) != roi_key_part(ring)
    assert cache_key("x", roi=roi_key_part(bbox)) != cache_key("x", roi=roi_key_part(ring))


def test_none_roi_is_stable() -> None:
    assert cache_key("x", roi=roi_key_part(None)) == cache_key("x", roi=None)


def test_algo_version_changes_keys(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    before = cache_key("x", dataset="s2")
    monkeypatch.setattr(cache, "ALGO_VERSION", 999)
    assert cache_key("x", dataset="s2") != before


def test_ttl_policy() -> None:
    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).date().isoformat()
    today = datetime.now(tz=UTC).date().isoformat()
    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).date().isoformat()
    assert ttl_for(yesterday) is None  # closed historical range: immutable
    assert ttl_for(today) == cache.OPEN_ENDED_TTL_SECONDS
    assert ttl_for(tomorrow) == cache.OPEN_ENDED_TTL_SECONDS
