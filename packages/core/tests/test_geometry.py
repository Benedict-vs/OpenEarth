from __future__ import annotations

import pytest

from openearth.errors import InvalidROIError
from openearth.geometry import BBox, PolygonROI


def test_bbox_validates_on_construction() -> None:
    with pytest.raises(InvalidROIError):
        BBox(10, 0, 5, 10)  # west >= east


def test_bbox_is_global() -> None:
    assert BBox(-180, -90, 180, 90).is_global
    assert BBox(-179.5, -89.5, 179.5, 89.5).is_global
    assert not BBox(-104.5, 31.0, -103.0, 32.5).is_global  # Permian Basin


def test_bbox_center_and_extent() -> None:
    box = BBox(8.58, 49.35, 8.77, 49.46)  # Heidelberg
    lat, lon = box.center
    assert lat == pytest.approx(49.405)
    assert lon == pytest.approx(8.675)
    assert box.width_deg == pytest.approx(0.19)
    assert box.height_deg == pytest.approx(0.11)


def test_bbox_aspect_ratio_cosine_corrected() -> None:
    # A 1°×1° box at the equator is square; at 60°N it is half as wide.
    assert BBox(0, -0.5, 1, 0.5).aspect_ratio() == pytest.approx(1.0, abs=1e-4)
    assert BBox(0, 59.5, 1, 60.5).aspect_ratio() == pytest.approx(0.5, abs=1e-2)


def test_bbox_rounded_for_cache_keys() -> None:
    box = BBox(8.5800001, 49.3499999, 8.77000004, 49.46)
    assert box.rounded().as_tuple() == (8.58, 49.35, 8.77, 49.46)


def test_polygon_closes_open_ring() -> None:
    poly = PolygonROI(((0, 0), (1, 0), (1, 1)))
    assert poly.ring[0] == poly.ring[-1]
    assert len(poly.ring) == 4


def test_polygon_rejects_degenerate() -> None:
    with pytest.raises(InvalidROIError):
        PolygonROI(((0, 0), (1, 0)))  # too few points
    with pytest.raises(InvalidROIError):
        PolygonROI(((0, 0), (1, 0), (2, 0)))  # zero height
    with pytest.raises(InvalidROIError):
        PolygonROI(((0, 0), (200, 0), (1, 1)))  # lon out of range


def test_polygon_bounds_and_geojson() -> None:
    poly = PolygonROI(((10, 20), (12, 20), (12, 23), (10, 23), (10, 20)))
    assert poly.bounds.as_tuple() == (10, 20, 12, 23)
    gj = poly.to_geojson()
    assert gj["type"] == "Polygon"
    assert gj["coordinates"][0][0] == gj["coordinates"][0][-1]
