"""Earth Engine provider for Sentinel-1 SAR GRD data."""

from __future__ import annotations

from datetime import date, datetime

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.providers.s1_registry import get_s1_band_config

# IW mode with VV+VH dual polarisation is the most globally
# available Sentinel-1 product and supports all derived variables.
_INSTRUMENT_MODE = "IW"
_REQUIRED_POLARISATIONS = ["VV", "VH"]


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
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Return a filtered Sentinel-1 GRD ImageCollection for *data_key*.

    The collection is restricted to IW mode images that carry both VV
    and VH polarisations so that all derived variables are available.
    """
    config = get_s1_band_config(data_key)
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    base = (
        ee.ImageCollection(config.collection_id)
        .filterDate(start, end)
        .filterBounds(geometry)
        .filter(ee.Filter.eq("instrumentMode", _INSTRUMENT_MODE))
        .filter(
            ee.Filter.listContains(
                "transmitterReceiverPolarisation", "VV",
            )
        )
        .filter(
            ee.Filter.listContains(
                "transmitterReceiverPolarisation", "VH",
            )
        )
    )

    if data_key == "VV":
        return base.select("VV")
    if data_key == "VH":
        return base.select("VH")
    if data_key == "VV_VH_RATIO":
        return base.map(_to_vv_vh_ratio)
    if data_key == "RVI":
        return base.map(_to_rvi)

    # Fallback — should never be reached if registry is consistent.
    raise ValueError(f"Unsupported S1 variable: {data_key!r}")
