from __future__ import annotations

from datetime import date

from openearth.catalog.presets import METHANE_SITES, ROI_PRESETS


def test_preset_counts() -> None:
    # 8 continents + 6 cities + 7 methane sites, verbatim from v1 config
    assert len(ROI_PRESETS) == 21
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
