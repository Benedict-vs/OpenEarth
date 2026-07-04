"""Wind-convention tests — the v1 direction bug regression suite.

v1 computed atan2(u, v) and called it "meteorological"; these cardinal
cases pin down both conventions so the mistake cannot recur silently.
"""

from __future__ import annotations

import numpy as np
import pytest

from openearth.methane.wind import WindSample, wind_from_deg, wind_speed, wind_to_deg

# (u, v) -> (blows toward, blows from)
CARDINALS = [
    pytest.param(1.0, 0.0, 90.0, 270.0, id="westerly (u=1): toward E, from W"),
    pytest.param(0.0, 1.0, 0.0, 180.0, id="southerly (v=1): toward N, from S"),
    pytest.param(-1.0, 0.0, 270.0, 90.0, id="easterly (u=-1): toward W, from E"),
    pytest.param(0.0, -1.0, 180.0, 0.0, id="northerly (v=-1): toward S, from N"),
]


@pytest.mark.parametrize(("u", "v", "to_deg", "from_deg"), CARDINALS)
def test_cardinal_conventions(u: float, v: float, to_deg: float, from_deg: float) -> None:
    assert wind_to_deg(u, v) == pytest.approx(to_deg)
    assert wind_from_deg(u, v) == pytest.approx(from_deg)


def test_diagonal() -> None:
    # u=v=1: blowing toward the north-east, i.e. from the south-west.
    assert wind_to_deg(1.0, 1.0) == pytest.approx(45.0)
    assert wind_from_deg(1.0, 1.0) == pytest.approx(225.0)


def test_range_is_0_360() -> None:
    for u, v in [(-1.0, -1.0), (-0.3, 0.9), (0.0, -2.5)]:
        assert 0.0 <= float(wind_to_deg(u, v)) < 360.0
        assert 0.0 <= float(wind_from_deg(u, v)) < 360.0


def test_speed_pythagorean() -> None:
    assert wind_speed(3.0, 4.0) == pytest.approx(5.0)


def test_array_input() -> None:
    u = np.array([1.0, 0.0, -1.0, 0.0])
    v = np.array([0.0, 1.0, 0.0, -1.0])
    np.testing.assert_allclose(wind_to_deg(u, v), [90.0, 0.0, 270.0, 180.0])
    np.testing.assert_allclose(wind_from_deg(u, v), [270.0, 180.0, 90.0, 0.0])


def test_windsample_from_uv() -> None:
    from datetime import UTC, datetime

    sample = WindSample.from_uv(datetime(2024, 6, 1, 7, 30, tzinfo=UTC), 3.0, 4.0, "test")
    assert sample.speed_ms == pytest.approx(5.0)
    assert sample.wind_from_deg == pytest.approx((sample.wind_to_deg + 180.0) % 360.0)
