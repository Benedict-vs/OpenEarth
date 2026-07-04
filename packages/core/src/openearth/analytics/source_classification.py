"""Rule-based methane source classification using S1 + S2 indicators.

Ported from v1. The S2 composite is pinned to L1C TOA: the MBSP and
thermal-SWIR thresholds below were tuned on TOA reflectance and would shift
on L2A surface reflectance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

import ee

from openearth.catalog.builtin.s2 import S2_COLLECTION_ID
from openearth.providers.s1 import get_s1_collection
from openearth.providers.s2 import get_s2_base_collection

if TYPE_CHECKING:
    from openearth.geometry import ROI


@dataclass(frozen=True)
class ClassificationThresholds:
    """Thresholds for source classification rules."""

    s1_vv_high: float = -10.0  # dB; above → strong backscatter
    s1_vv_low: float = -18.0  # dB; below → smooth / water
    ndvi_veg: float = 0.35  # above → vegetation
    ndwi_water: float = 0.1  # above → water
    methane_signal: float = -0.02  # MBSP below this → methane
    thermal_b11: float = 0.5  # B11 above → thermal source (flare)
    thermal_b12: float = 0.5  # B12 above → thermal source (flare)
    geo_methane_strong: float = -0.04  # stricter MBSP for geo seep
    ndvi_barren: float = 0.1  # NDVI below → barren desert


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
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    thresholds: ClassificationThresholds | None = None,
) -> ee.Image:
    """Classify methane source types using rule-based logic.

    Combines S1 VV backscatter, NDVI, NDWI, and MBSP to produce a
    single-band ``ee.Image`` named ``SOURCE_CLASS`` with integer values
    1–5 corresponding to :data:`CLASS_LABELS`.
    """
    if thresholds is None:
        thresholds = ClassificationThresholds()

    # S1 VV mean composite.
    s1_col = get_s1_collection("VV", roi, start_date, end_date)
    s1_vv = s1_col.mean().select("VV")

    # S2 base collection for NDVI, NDWI, MBSP (TOA — see module docstring).
    s2_base = get_s2_base_collection(roi, start_date, end_date, collection_id=S2_COLLECTION_ID)
    s2_mean = s2_base.select(["B3", "B4", "B8", "B11", "B12"]).mean()

    b3 = s2_mean.select("B3")
    b4 = s2_mean.select("B4")
    b8 = s2_mean.select("B8")
    b11 = s2_mean.select("B11")
    b12 = s2_mean.select("B12")

    ndvi = b8.subtract(b4).divide(b8.add(b4)).rename("NDVI")
    ndwi = b3.subtract(b8).divide(b3.add(b8)).rename("NDWI")
    mbsp = b12.subtract(b11).divide(b11).rename("MBSP")

    # Boolean masks.
    has_methane = mbsp.lt(thresholds.methane_signal)
    high_s1 = s1_vv.gt(thresholds.s1_vv_high)
    low_s1 = s1_vv.lt(thresholds.s1_vv_low)
    high_ndvi = ndvi.gt(thresholds.ndvi_veg)
    low_ndvi = ndvi.lt(thresholds.ndvi_veg)
    high_ndwi = ndwi.gt(thresholds.ndwi_water)

    # Thermal anomaly: both SWIR bands abnormally high → active
    # combustion (gas flaring). Normal surfaces stay below ~0.3.
    is_thermal = b11.gt(thresholds.thermal_b11).And(b12.gt(thresholds.thermal_b12))

    # Barren desert: extremely low NDVI (sand/rock).
    is_barren = ndvi.lt(thresholds.ndvi_barren)

    # Stricter MBSP threshold for geological seep classification.
    has_strong_methane = mbsp.lt(thresholds.geo_methane_strong)

    # Start with "no signal" (class 5).
    classification = ee.Image.constant(5).toInt().rename("SOURCE_CLASS")

    # Apply rules in ascending priority order.
    # Rule 4: Geological seep — requires stronger MBSP signal and excludes
    # true barren desert (NDVI < ndvi_barren).
    is_geological = (
        has_strong_methane.And(low_ndvi).And(is_barren.Not()).And(low_s1.Not()).And(high_s1.Not())
    )
    classification = classification.where(is_geological, 4)

    # Rule 3: Wetland / water.
    is_wetland = has_methane.And(high_ndwi.Or(low_s1))
    classification = classification.where(is_wetland, 3)

    # Rule 2: Biogenic / agricultural.
    is_biogenic = has_methane.And(high_ndvi)
    classification = classification.where(is_biogenic, 2)

    # Rule 1: Industrial (highest priority). Two paths: methane-based
    # (pipeline leaks, valves) and thermal-based (gas flares where MBSP
    # goes positive).
    industrial_methane = has_methane.And(high_s1).And(low_ndvi)
    industrial_thermal = is_thermal.And(high_s1).And(low_ndvi)
    is_industrial = industrial_methane.Or(industrial_thermal)
    classification = classification.where(is_industrial, 1)

    return classification.clip(roi.to_ee_geometry())
