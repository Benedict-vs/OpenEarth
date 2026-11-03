from __future__ import annotations

from datetime import date
from itertools import pairwise

from openearth.catalog.presets import METHANE_SITES, ROI_PRESETS


def test_preset_counts() -> None:
    # 8 continents + 6 cities + 7 methane sites (verbatim from v1 config) + 1 country
    assert len(ROI_PRESETS) == 22
    assert len(METHANE_SITES) == 7


def test_known_sites_present() -> None:
    for name in (
        "CH4: Korpezhe, Turkmenistan",
        "CH4: Galkynysh, Turkmenistan",
        "CH4: Permian Basin (USA)",
        "CH4: Hassi Messaoud, Algeria",
        "CH4: Basra oil fields, Iraq",
        "CH4: Four Corners (USA)",
        "CH4: Upper Silesia, Poland",
    ):
        assert name in METHANE_SITES


def test_all_bboxes_valid() -> None:
    # BBox validates in __post_init__; just touch every preset.
    for preset in ROI_PRESETS.values():
        assert preset.bbox.width_deg > 0
        assert preset.bbox.height_deg > 0


def test_methane_sites_have_parseable_date_hints() -> None:
    for preset in METHANE_SITES.values():
        assert preset.date_hint is not None, f"{preset.name} lacks a date hint"
        start, end = (date.fromisoformat(d) for d in preset.date_hint)
        assert start < end


def test_only_methane_sites_have_hints() -> None:
    for preset in ROI_PRESETS.values():
        if preset.category != "methane_site":
            assert preset.date_hint is None


def test_entire_earth_is_global() -> None:
    assert ROI_PRESETS["Entire Earth"].bbox.is_global
    assert not ROI_PRESETS["Heidelberg (Germany)"].bbox.is_global


def test_china_polygon_preset() -> None:
    china = ROI_PRESETS["China"]
    assert china.category == "country"
    assert china.polygon is not None
    # bbox is the polygon's bounds: Pamirs → Heilongjiang/Ussuri, Hainan → Mohe.
    assert china.bbox == china.polygon.bounds
    assert 73 < china.bbox.west < 74
    assert 134 < china.bbox.east < 135.5
    assert 18 < china.bbox.south < 18.5
    assert 53 < china.bbox.north < 54
    # Beijing, Urumqi, Lhasa and Haikou (Hainan, via the strait bridge) lie inside.
    for lon, lat in ((116.4, 39.9), (87.6, 43.8), (91.1, 29.65), (110.35, 20.0)):
        assert _point_in_ring(lon, lat, china.polygon.ring), (lon, lat)
    # Seoul, Hanoi, Taipei and Ulaanbaatar do not.
    for lon, lat in ((126.98, 37.57), (105.85, 21.03), (121.56, 25.03), (106.9, 47.9)):
        assert not _point_in_ring(lon, lat, china.polygon.ring), (lon, lat)


def _point_in_ring(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in pairwise(ring):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside
