"""Landsat provider: offline routing, per-spacecraft band maps, QA bits, SLC guard.

All EE-free: dispatch routing, the L5/7 vs L8/9 RGB band-number shift, the
QA_PIXEL cloud mask on a synthetic array, and the pure SLC-off wedge-gap advisory.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pytest

import openearth.providers as providers
from openearth.catalog.builtin.optical import CANONICAL_BANDS
from openearth.geometry import BBox
from openearth.providers.landsat import (
    L57_BAND_MAP,
    L89_BAND_MAP,
    MIN_SLC_OFF_COMPOSITE_SCENES,
    QA_PIXEL_CLOUD_BITS,
    SLC_OFF_DATE,
    advisory_from_metadata,
    is_slc_off,
    slc_off_advisory,
)
from openearth.providers.qa import clear_pixels


def test_dispatcher_routes_landsat_to_merged_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_landsat(product_key: str, *args: Any, **kwargs: Any) -> str:
        seen["route"] = product_key
        return "landsat-collection"

    monkeypatch.setattr(providers, "get_landsat_collection", fake_landsat)
    roi = BBox(-115.4, 36.0, -115.0, 36.3)
    result = providers.get_collection("RGB", roi, "1990-01-01", "1991-01-01", source="landsat")
    assert result == "landsat-collection"
    assert seen["route"] == "RGB"


def test_per_spacecraft_band_maps_cover_canonical() -> None:
    assert set(L57_BAND_MAP.values()) == set(CANONICAL_BANDS)
    assert set(L89_BAND_MAP.values()) == set(CANONICAL_BANDS)


def test_rgb_band_numbering_shifts_between_l57_and_l89() -> None:
    # The documented shift: L5/7 RGB = SR_B3/SR_B2/SR_B1; L8/9 = SR_B4/SR_B3/SR_B2.
    assert (L57_BAND_MAP["SR_B3"], L57_BAND_MAP["SR_B2"], L57_BAND_MAP["SR_B1"]) == (
        "RED",
        "GREEN",
        "BLUE",
    )
    assert (L89_BAND_MAP["SR_B4"], L89_BAND_MAP["SR_B3"], L89_BAND_MAP["SR_B2"]) == (
        "RED",
        "GREEN",
        "BLUE",
    )
    # NIR shifts SR_B4 (L5/7) → SR_B5 (L8/9) because the thermal band inserts at B6.
    assert L57_BAND_MAP["SR_B4"] == "NIR"
    assert L89_BAND_MAP["SR_B5"] == "NIR"
    # SWIR2 is SR_B7 on every spacecraft.
    assert L57_BAND_MAP["SR_B7"] == "SWIR2"
    assert L89_BAND_MAP["SR_B7"] == "SWIR2"


def test_qa_pixel_clear_mask_matches_hand_computed() -> None:
    # QA_PIXEL bits: 1 dilated cloud, 3 cloud, 4 cloud shadow (masked); 5 snow (kept).
    assert QA_PIXEL_CLOUD_BITS == (1, 3, 4)
    qa = np.array(
        [
            0,  # clear
            1 << 1,  # dilated cloud → masked
            1 << 3,  # cloud → masked
            1 << 4,  # cloud shadow → masked
            1 << 5,  # snow → clear (landscape)
            (1 << 3) | (1 << 5),  # cloud + snow → masked
        ],
        dtype=np.int32,
    )
    expected = np.array([True, False, False, False, True, False])
    np.testing.assert_array_equal(clear_pixels(qa, QA_PIXEL_CLOUD_BITS), expected)


def test_is_slc_off_only_l7_after_failure() -> None:
    assert SLC_OFF_DATE.isoformat() == "2003-05-31"
    assert is_slc_off("LE07", date(2010, 6, 1)) is True
    assert is_slc_off("LE07", date(2001, 1, 1)) is False
    assert is_slc_off("LE07", SLC_OFF_DATE) is False  # strictly after
    assert is_slc_off("LE07", date(2003, 6, 1)) is True
    assert is_slc_off("LC08", date(2015, 1, 1)) is False
    assert is_slc_off("LT05", date(2005, 1, 1)) is False


def test_slc_off_advisory_only_fires_on_thin_l7_only_windows() -> None:
    late = date(2010, 6, 1)
    early = date(2001, 6, 1)
    l8 = date(2015, 6, 1)
    # Empty window → no advisory.
    assert slc_off_advisory([], []) is None
    # One / two SLC-off L7 scenes only → wedge gaps, advisory fires.
    assert slc_off_advisory(["LE07"], [late]) is not None
    assert slc_off_advisory(["LE07", "LE07"], [late, late]) is not None
    # Enough L7 scenes to composite the wedges away → no advisory.
    assert (
        slc_off_advisory(
            ["LE07"] * MIN_SLC_OFF_COMPOSITE_SCENES, [late] * MIN_SLC_OFF_COMPOSITE_SCENES
        )
        is None
    )
    # Any non-SLC-off scene present → the composite fills, no advisory.
    assert slc_off_advisory(["LE07", "LC08"], [late, l8]) is None
    # Pre-2003 L7 is intact (not SLC-off) → no advisory even alone.
    assert slc_off_advisory(["LE07"], [early]) is None


def test_advisory_from_metadata_normalizes_the_ee_payload() -> None:
    # The exact shape one getInfo on scene_metadata returns (verified live).
    fires = advisory_from_metadata({"spacecraft": ["LANDSAT_7"], "acquired": ["2008-07-01"]})
    assert fires is not None
    assert "wedge" in fires
    # A companion spacecraft in the window → composite fills, no advisory.
    assert (
        advisory_from_metadata(
            {"spacecraft": ["LANDSAT_7", "LANDSAT_5"], "acquired": ["2008-07-01", "2008-07-03"]}
        )
        is None
    )


def test_advisory_from_metadata_drops_unknown_and_unparseable_scenes() -> None:
    # Unknown spacecraft ids and bad dates are dropped, not guessed at: the one
    # usable scene left is SLC-off L7 → advisory still fires.
    assert (
        advisory_from_metadata(
            {
                "spacecraft": ["LANDSAT_99", "LANDSAT_7", "LANDSAT_7"],
                "acquired": ["2008-07-01", "not-a-date", "2008-07-05"],
            }
        )
        is not None
    )
    # Nothing usable at all → silent.
    only_unknown = {"spacecraft": ["LANDSAT_99"], "acquired": ["2008-07-01"]}
    assert advisory_from_metadata(only_unknown) is None
    assert advisory_from_metadata(None) is None
    assert advisory_from_metadata({}) is None
