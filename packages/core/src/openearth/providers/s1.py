"""Earth Engine provider for Sentinel-1 SAR GRD data.

v2 changes vs the v1 provider:

- **Orbit-pass filtering** (``orbit_pass="ASCENDING"|"DESCENDING"`` and
  ``relative_orbit=``). v1 mixed ascending and descending acquisitions in
  one composite, blending opposing viewing geometries.
- **Optional speckle reduction** (``speckle_radius_m=`` applies a circular
  focal median per scene before any derived computation).
- Honest naming: ``VV_VH_RATIO`` is the dB *difference* VV − VH (see the
  catalog entry).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.registry import get_product

if TYPE_CHECKING:
    from openearth.geometry import ROI

# IW mode with VV+VH dual polarisation is the most globally available
# Sentinel-1 product and supports all derived variables.
_INSTRUMENT_MODE = "IW"

OrbitPass = Literal["ASCENDING", "DESCENDING"]


def _to_vv_vh_ratio(image: ee.Image) -> ee.Image:
    """Compute VV − VH (dB), equivalent to log10(VV_lin / VH_lin)."""
    return (
        image.select("VV")
        .subtract(image.select("VH"))
        .rename("VV_VH_RATIO")
        .copyProperties(image, ["system:time_start"])
    )


def _to_rvi(image: ee.Image) -> ee.Image:
    """Compute Radar Vegetation Index.

    RVI = 4 · VH_lin / (VV_lin + VH_lin)

    Inputs are in dB; convert to linear power before the ratio.
    Result is dimensionless, range [0, 1].
    """
    vv_lin = ee.Image(10).pow(image.select("VV").divide(10))
    vh_lin = ee.Image(10).pow(image.select("VH").divide(10))
    rvi = vh_lin.multiply(4).divide(vv_lin.add(vh_lin)).rename("RVI")
    return rvi.copyProperties(image, ["system:time_start"])


def get_s1_collection(
    data_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    orbit_pass: OrbitPass | None = None,
    relative_orbit: int | None = None,
    speckle_radius_m: float | None = None,
) -> ee.ImageCollection:
    """Return a filtered Sentinel-1 GRD ImageCollection for *data_key*.

    Restricted to IW-mode images carrying both VV and VH polarisations so
    that all derived variables are available. Pass *orbit_pass* (and
    optionally *relative_orbit*) to keep a single viewing geometry —
    strongly recommended for any change/temporal analysis.
    """
    config = get_product("s1", data_key)
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    base = (
        ee.ImageCollection(config.collection_id)
        .filterDate(start, end)
        .filterBounds(roi.to_ee_geometry())
        .filter(ee.Filter.eq("instrumentMode", _INSTRUMENT_MODE))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )
    if orbit_pass is not None:
        base = base.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))
    if relative_orbit is not None:
        base = base.filter(ee.Filter.eq("relativeOrbitNumber_start", relative_orbit))
    if speckle_radius_m is not None:
        radius = speckle_radius_m

        def _despeckle(image: ee.Image) -> ee.Image:
            smoothed = image.select(["VV", "VH"]).focalMedian(radius, "circle", "meters")
            return image.addBands(smoothed, overwrite=True)

        base = base.map(_despeckle)

    if data_key == "VV":
        return base.select("VV")
    if data_key == "VH":
        return base.select("VH")
    if data_key == "VV_VH_RATIO":
        return base.map(_to_vv_vh_ratio)
    if data_key == "RVI":
        return base.map(_to_rvi)

    # Fallback — should never be reached if the catalog is consistent.
    raise ValueError(f"Unsupported S1 variable: {data_key!r}")
