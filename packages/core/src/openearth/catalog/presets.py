"""ROI presets and methane watch sites (ported from the v1 app config).

These seed the v2 sites/presets database tables (``scripts/seed_db.py``);
the values are carried over verbatim from the retired v1 app config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from openearth.geometry import BBox

Category = Literal["continent", "city", "methane_site"]


@dataclass(frozen=True)
class RoiPreset:
    name: str
    bbox: BBox
    category: Category
    # Suggested (start, end) ISO dates — methane sites carry known-event windows.
    date_hint: tuple[str, str] | None = None


def _preset(
    name: str,
    west: float,
    south: float,
    east: float,
    north: float,
    category: Category,
    date_hint: tuple[str, str] | None = None,
) -> RoiPreset:
    return RoiPreset(name, BBox(west, south, east, north), category, date_hint)


ROI_PRESETS: dict[str, RoiPreset] = {
    p.name: p
    for p in [
        # Continents
        _preset("Europe", -25.0, 34.0, 45.0, 72.0, "continent"),
        _preset("North America", -170.0, 15.0, -50.0, 72.0, "continent"),
        _preset("South America", -82.0, -56.0, -34.0, 13.0, "continent"),
        _preset("Africa", -18.0, -35.0, 52.0, 37.0, "continent"),
        _preset("Asia", 25.0, -10.0, 180.0, 75.0, "continent"),
        _preset("Oceania", 110.0, -50.0, 180.0, 0.0, "continent"),
        _preset("Antarctica", -180.0, -90.0, 180.0, -60.0, "continent"),
        _preset("Entire Earth", -180.0, -90.0, 180.0, 90.0, "continent"),
        # Cities
        _preset("Heidelberg (Germany)", 8.58, 49.35, 8.77, 49.46, "city"),
        _preset("London (UK)", -0.51, 51.28, 0.33, 51.70, "city"),
        _preset("Berlin (Germany)", 13.09, 52.33, 13.76, 52.68, "city"),
        _preset("New York (USA)", -74.26, 40.49, -73.69, 40.92, "city"),
        _preset("Merida (Mexico)", -89.80, 20.85, -89.50, 21.10, "city"),
        _preset("Barranquilla (Colombia)", -74.93, 10.90, -74.70, 11.10, "city"),
        # Methane emission sites (with known-event date hints)
        _preset(
            "CH4: Korpezhe, Turkmenistan",
            53.7,
            38.2,
            54.7,
            38.8,
            "methane_site",
            ("2024-06-01", "2024-12-01"),
        ),
        _preset(
            "CH4: Galkynysh, Turkmenistan",
            61.8,
            36.9,
            62.9,
            37.7,
            "methane_site",
            ("2024-06-01", "2024-12-01"),
        ),
        _preset(
            "CH4: Permian Basin (USA)",
            -104.5,
            31.0,
            -103.0,
            32.5,
            "methane_site",
            ("2024-03-01", "2024-09-01"),
        ),
        _preset(
            "CH4: Hassi Messaoud, Algeria",
            5.4,
            31.2,
            6.4,
            32.0,
            "methane_site",
            ("2024-04-01", "2024-10-01"),
        ),
        _preset(
            "CH4: Basra oil fields, Iraq",
            46.9,
            30.0,
            47.8,
            31.0,
            "methane_site",
            ("2024-05-01", "2024-11-01"),
        ),
        _preset(
            "CH4: Four Corners (USA)",
            -109.6,
            36.5,
            -108.5,
            37.5,
            "methane_site",
            ("2024-03-01", "2024-09-01"),
        ),
        _preset(
            "CH4: Upper Silesia, Poland",
            18.5,
            50.0,
            19.5,
            50.5,
            "methane_site",
            ("2024-04-01", "2024-10-01"),
        ),
    ]
}

METHANE_SITES: dict[str, RoiPreset] = {
    name: p for name, p in ROI_PRESETS.items() if p.category == "methane_site"
}
