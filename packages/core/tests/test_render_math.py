"""Pure rendering math (no Earth Engine round-trips)."""

from __future__ import annotations

from openearth.ee.render import geo_dimensions
from openearth.geometry import BBox


def test_square_at_equator() -> None:
    assert geo_dimensions(BBox(0, -0.5, 1, 0.5), 1024) == "1024x1024"


def test_wide_box_pins_width() -> None:
    dims = geo_dimensions(BBox(0, 0, 10, 1), 1024)
    w, h = (int(x) for x in dims.split("x"))
    assert w == 1024
    assert h < 1024


def test_high_latitude_cosine_shrinks_width() -> None:
    # 1°×1° at 60°N: real width is ~half the height → portrait output.
    dims = geo_dimensions(BBox(0, 59.5, 1, 60.5), 1000)
    w, h = (int(x) for x in dims.split("x"))
    assert h == 1000
    assert 480 <= w <= 520


def test_minimum_one_pixel() -> None:
    dims = geo_dimensions(BBox(0, 0, 179, 0.001), 512)
    w, h = (int(x) for x in dims.split("x"))
    assert w == 512
    assert h >= 1
