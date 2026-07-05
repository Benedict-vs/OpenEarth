"""Live EE smoke test for the API (never in CI).

Run: OPENEARTH_EE_TESTS=1 uv run pytest -m ee packages/api/tests
Needs real `earthengine authenticate` credentials + OPENEARTH_EE_PROJECT.
"""

from __future__ import annotations

import math
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from openearth_api.app import create_app

pytestmark = [
    pytest.mark.ee,
    pytest.mark.skipif(
        os.environ.get("OPENEARTH_EE_TESTS") != "1",
        reason="live EE tests need OPENEARTH_EE_TESTS=1 and real credentials",
    ),
]

HEIDELBERG = {"kind": "bbox", "west": 8.58, "south": 49.35, "east": 8.77, "north": 49.46}


def _tile_xyz(lon: float, lat: float, zoom: int) -> tuple[int, int, int]:
    """Web-mercator tile containing (lon, lat)."""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return zoom, x, y


def test_tiles_mint_and_first_tile_renders() -> None:
    # Default (env-derived) settings: real EE project, real data dir.
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/tiles",
            json={
                "dataset": "s2",
                "product": "NDVI",
                "roi": HEIDELBERG,
                "dates": {"start": "2024-06-01", "end": "2024-08-01"},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "{z}" in body["tile_url"]
        assert body["legend"]["palette"]

        z, x, y = _tile_xyz(8.68, 49.41, 12)
        tile_url = body["tile_url"].format(z=z, x=x, y=y)
        tile = httpx.get(tile_url, timeout=120, follow_redirects=True)
        assert tile.status_code == 200
        assert tile.headers["content-type"].startswith("image/")
