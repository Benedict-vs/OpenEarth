"""Rule-based methane source classification using S1 + S2 indicators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import ee

from openearth.providers.gee_s1 import get_s1_collection
from openearth.providers.gee_s2 import _get_s2_base_collection


@dataclass(frozen=True)
class ClassificationThresholds:
    """Thresholds for source classification rules."""

    s1_vv_high: float = -10.0      # dB; above → strong backscatter
    s1_vv_low: float = -18.0       # dB; below → smooth / water
    ndvi_veg: float = 0.35         # above → vegetation
    ndwi_water: float = 0.1        # above → water
    methane_signal: float = -0.02  # MBSP below this → methane


CLASS_LABELS = {
    1: "Industrial / Oil & Gas",
    2: "Biogenic / Agricultural",
    3: "Wetland / Water",
    4: "Geological Seep",
    5: "No signal",
}

CLASS_PALETTE = [
    "#e41a1c",  # 1: Industrial — red
    "#4daf4a",  # 2: Biogenic — green
    "#377eb8",  # 3: Wetland — blue
    "#984ea3",  # 4: Geological — purple
    "#999999",  # 5: No signal — grey
]


def classify_methane_sources(
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    thresholds: ClassificationThresholds | None = None,
) -> ee.Image:
    """Classify methane source types using rule-based logic.

    Combines S1 VV backscatter, NDVI, NDWI, and MBSP to
    produce a single-band ``ee.Image`` named ``SOURCE_CLASS``
    with integer values 1–5 corresponding to
    :data:`CLASS_LABELS`.
    """
    if thresholds is None:
        thresholds = ClassificationThresholds()

    # S1 VV mean composite.
    s1_col = get_s1_collection(
        "VV", geometry, start_date, end_date,
    )
    s1_vv = s1_col.mean().select("VV")

    # S2 base collection for NDVI, NDWI, MBSP.
    s2_base = _get_s2_base_collection(
        geometry, start_date, end_date,
    )
    s2_mean = s2_base.select(
        ["B3", "B4", "B8", "B11", "B12"],
    ).mean()

    b3 = s2_mean.select("B3")
    b4 = s2_mean.select("B4")
    b8 = s2_mean.select("B8")
    b11 = s2_mean.select("B11")
    b12 = s2_mean.select("B12")

    ndvi = b8.subtract(b4).divide(b8.add(b4)).rename("NDVI")
    ndwi = b3.subtract(b8).divide(b3.add(b8)).rename("NDWI")
    mbsp = (
        b12.subtract(b11).divide(b11).rename("MBSP")
    )

    # Boolean masks.
    has_methane = mbsp.lt(thresholds.methane_signal)
    high_s1 = s1_vv.gt(thresholds.s1_vv_high)
    low_s1 = s1_vv.lt(thresholds.s1_vv_low)
    high_ndvi = ndvi.gt(thresholds.ndvi_veg)
    low_ndvi = ndvi.lt(thresholds.ndvi_veg)
    high_ndwi = ndwi.gt(thresholds.ndwi_water)

    # Start with "no signal" (class 5).
    classification = (
        ee.Image.constant(5).toInt()
        .rename("SOURCE_CLASS")
    )

    # Apply rules in ascending priority order.
    # Rule 4: Geological seep.
    is_geological = (
        has_methane
        .And(low_ndvi)
        .And(low_s1.Not())
        .And(high_s1.Not())
    )
    classification = classification.where(
        is_geological, 4,
    )

    # Rule 3: Wetland / water.
    is_wetland = has_methane.And(
        high_ndwi.Or(low_s1),
    )
    classification = classification.where(
        is_wetland, 3,
    )

    # Rule 2: Biogenic / agricultural.
    is_biogenic = has_methane.And(high_ndvi)
    classification = classification.where(
        is_biogenic, 2,
    )

    # Rule 1: Industrial (highest priority).
    is_industrial = (
        has_methane.And(high_s1).And(low_ndvi)
    )
    classification = classification.where(
        is_industrial, 1,
    )

    return classification.clip(geometry)
