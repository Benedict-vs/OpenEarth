"""HLS provider: offline routing, band-map integrity, and Fmask bit math.

The EE collection build itself is exercised by the live smoke; here we verify
everything runnable without Earth Engine: the dispatcher routes ``hls`` to the
merged provider, the per-sensor canonical band maps are complete and consistent,
and the Fmask cloud mask matches a hand-computed synthetic QA array bit-for-bit.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

import openearth.providers as providers
from openearth.catalog.builtin.optical import CANONICAL_BANDS
from openearth.geometry import BBox
from openearth.providers.hls import (
    FMASK_CLOUD_BITS,
    L30_BAND_MAP,
    S30_BAND_MAP,
)
from openearth.providers.qa import bit_mask, clear_pixels


def test_dispatcher_routes_hls_to_merged_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_hls(product_key: str, *args: Any, **kwargs: Any) -> str:
        seen["route"] = product_key
        return "hls-collection"

    monkeypatch.setattr(providers, "get_hls_collection", fake_hls)
    roi = BBox(-103.5, 31.7, -103.3, 31.9)
    result = providers.get_collection("RGB", roi, "2023-07-01", "2023-08-01", source="hls")
    assert result == "hls-collection"
    assert seen["route"] == "RGB"


def test_band_maps_cover_the_canonical_scheme() -> None:
    # Both sensors must map to exactly the canonical band set (order-independent),
    # so the shared RGB/NDVI/NDWI recipes resolve on either.
    assert set(L30_BAND_MAP.values()) == set(CANONICAL_BANDS)
    assert set(S30_BAND_MAP.values()) == set(CANONICAL_BANDS)
    # RGB is harmonized to the same native names on both HLS sensors.
    for native, canon in (("B4", "RED"), ("B3", "GREEN"), ("B2", "BLUE")):
        assert L30_BAND_MAP[native] == canon
        assert S30_BAND_MAP[native] == canon
    # NIR differs by sensor (L30 has no B8A; S30 has no B5).
    assert L30_BAND_MAP["B5"] == "NIR"
    assert S30_BAND_MAP["B8A"] == "NIR"
    assert "B5" not in S30_BAND_MAP
    assert "B8A" not in L30_BAND_MAP


def test_fmask_cloud_bits_are_cloud_adjacent_shadow() -> None:
    assert FMASK_CLOUD_BITS == (1, 2, 3)
    assert bit_mask(FMASK_CLOUD_BITS) == 0b1110  # 14


def test_fmask_clear_mask_matches_hand_computed() -> None:
    # Fmask bits: 1 cloud, 2 adjacent, 3 shadow (masked); 4 snow, 5 water,
    # 6 aerosol (kept as landscape).
    qa = np.array(
        [
            0,  # nothing set → clear
            1 << 1,  # cloud → masked
            1 << 2,  # adjacent → masked
            1 << 3,  # shadow → masked
            1 << 4,  # snow → clear (landscape)
            1 << 5,  # water → clear (landscape)
            1 << 6,  # aerosol level → clear
            (1 << 1) | (1 << 4),  # cloud + snow → masked (cloud dominates)
        ],
        dtype=np.int32,
    )
    expected = np.array([True, False, False, False, True, True, True, False])
    np.testing.assert_array_equal(clear_pixels(qa, FMASK_CLOUD_BITS), expected)
