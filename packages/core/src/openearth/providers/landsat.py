"""Earth Engine provider for Landsat Collection 2 Level-2 — deep history to 1984.

Merges LT05 / LE07 / LC08 / LC09 surface-reflectance archives per window. The RGB
band numbering shifts between L5/7 and L8/9 (a thermal band was inserted at B6),
so bands are mapped per-spacecraft to the canonical scheme (RED/GREEN/BLUE/NIR/
SWIR1/SWIR2); product keys stay uniform. SR values are scaled ``×0.0000275 − 0.2``
to reflectance; QA_PIXEL bits 1|3|4 (dilated cloud, cloud, cloud shadow) mask
clouds while snow (bit 5) is kept as landscape.

Post-2003 Landsat-7 carries SLC-off wedge gaps (~22 % scene loss); a lone L7
frame is never trustworthy — :func:`slc_off_advisory` is the pure guard the frame
builder / preflight consult to warn when a window would render wedge gaps.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.builtin.landsat import (
    LC08_COLLECTION_ID,
    LC09_COLLECTION_ID,
    LE07_COLLECTION_ID,
    LT05_COLLECTION_ID,
)
from openearth.catalog.registry import get_product
from openearth.providers.generic import _compute_product
from openearth.providers.qa import bit_mask

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from openearth.geometry import ROI

# Collection 2 surface-reflectance DN → reflectance.
SR_SCALE = 0.0000275
SR_OFFSET = -0.2

# QA_PIXEL bits: 1 dilated cloud, 3 cloud, 4 cloud shadow, 5 snow. Mask 1|3|4;
# snow is landscape (kept), matching the HLS snow/water policy.
QA_PIXEL_BAND = "QA_PIXEL"
QA_PIXEL_CLOUD_BITS: tuple[int, ...] = (1, 3, 4)

# Per-spacecraft SR band → canonical name. L5/7 and L8/9 differ by the B6 thermal
# insertion, so the visible/NIR/SWIR numbering shifts by one from B4 onward.
L57_BAND_MAP: dict[str, str] = {
    "SR_B3": "RED",
    "SR_B2": "GREEN",
    "SR_B1": "BLUE",
    "SR_B4": "NIR",
    "SR_B5": "SWIR1",
    "SR_B7": "SWIR2",
}
L89_BAND_MAP: dict[str, str] = {
    "SR_B4": "RED",
    "SR_B3": "GREEN",
    "SR_B2": "BLUE",
    "SR_B5": "NIR",
    "SR_B6": "SWIR1",
    "SR_B7": "SWIR2",
}

# Short spacecraft tags (match the collection id prefixes) and the SLC failure.
SLC_OFF_DATE = date(2003, 5, 31)
MIN_SLC_OFF_COMPOSITE_SCENES = 3

_SENSORS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("LT05", LT05_COLLECTION_ID, L57_BAND_MAP),
    ("LE07", LE07_COLLECTION_ID, L57_BAND_MAP),
    ("LC08", LC08_COLLECTION_ID, L89_BAND_MAP),
    ("LC09", LC09_COLLECTION_ID, L89_BAND_MAP),
)


def is_slc_off(spacecraft: str, acquired: date) -> bool:
    """True for Landsat-7 acquisitions after the 2003-05-31 SLC failure (wedge gaps)."""
    return spacecraft == "LE07" and acquired > SLC_OFF_DATE


def slc_off_advisory(spacecrafts: Sequence[str], dates: Sequence[date]) -> str | None:
    """Warn when a window's usable Landsat scenes would render with wedge gaps.

    Pure — the frame builder / preflight feed it a window's per-scene spacecraft
    tags + acquisition dates. Returns a message when every usable scene is
    SLC-off Landsat-7 and there are too few to fill the wedges by compositing
    (< :data:`MIN_SLC_OFF_COMPOSITE_SCENES`), else ``None``.
    """
    pairs = list(zip(spacecrafts, dates, strict=True))
    if not pairs:
        return None
    slc_off = sum(1 for sc, d in pairs if is_slc_off(sc, d))
    non_slc_off = len(pairs) - slc_off
    if non_slc_off == 0 and len(pairs) < MIN_SLC_OFF_COMPOSITE_SCENES:
        return (
            f"Only {len(pairs)} Landsat-7 SLC-off scene(s) in this window — the "
            "composite will show diagonal wedge gaps. Widen the window to "
            f"≥{MIN_SLC_OFF_COMPOSITE_SCENES} scenes or include another Landsat "
            "spacecraft."
        )
    return None


# EE SPACECRAFT_ID property values → our short tags (the collection id prefixes).
SPACECRAFT_TAGS: dict[str, str] = {
    "LANDSAT_5": "LT05",
    "LANDSAT_7": "LE07",
    "LANDSAT_8": "LC08",
    "LANDSAT_9": "LC09",
}


def scene_metadata(
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.Dictionary:
    """Spacecraft tags + acquisition dates of the raw scenes in a window.

    Aggregate-only probe over the unprepped archives (the prepped collections
    keep only ``system:time_start``, so the identifying properties must come
    from the raw images). One ``getInfo`` on the result yields
    ``{"spacecraft": ["LANDSAT_7", …], "acquired": ["2008-07-01", …]}`` for
    :func:`advisory_from_metadata`.
    """
    geometry = roi.to_ee_geometry()
    start, end = to_ee_date(start_date), to_ee_date(end_date)
    merged = ee.ImageCollection(_SENSORS[0][1]).filterDate(start, end).filterBounds(geometry)
    for _, collection_id, _ in _SENSORS[1:]:
        merged = merged.merge(
            ee.ImageCollection(collection_id).filterDate(start, end).filterBounds(geometry)
        )
    return ee.Dictionary(
        {
            "spacecraft": merged.aggregate_array("SPACECRAFT_ID"),
            "acquired": merged.aggregate_array("DATE_ACQUIRED"),
        }
    )


def advisory_from_metadata(info: Mapping[str, Sequence[str]] | None) -> str | None:
    """Pure: a fetched :func:`scene_metadata` payload → :func:`slc_off_advisory`.

    Tolerant of the EE property payload — scenes with an unknown spacecraft id
    or an unparseable date are dropped rather than guessed at.
    """
    if not info:
        return None
    spacecrafts: list[str] = []
    dates: list[date] = []
    for raw_tag, raw_date in zip(info.get("spacecraft", []), info.get("acquired", []), strict=True):
        tag = SPACECRAFT_TAGS.get(raw_tag)
        if tag is None:
            continue
        try:
            acquired = date.fromisoformat(raw_date)
        except ValueError:
            continue
        spacecrafts.append(tag)
        dates.append(acquired)
    return slc_off_advisory(spacecrafts, dates)


def _prep_sensor(
    collection_id: str,
    band_map: dict[str, str],
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Filter one Landsat archive, QA-mask clouds, SR-scale, rename to canonical."""
    src_bands = list(band_map)
    canonical = [band_map[b] for b in src_bands]

    def _prep(image: ee.Image) -> ee.Image:
        clear = image.select(QA_PIXEL_BAND).bitwiseAnd(bit_mask(QA_PIXEL_CLOUD_BITS)).eq(0)
        reflectance = image.select(src_bands, canonical).multiply(SR_SCALE).add(SR_OFFSET)
        return ee.Image(reflectance.updateMask(clear).copyProperties(image, ["system:time_start"]))

    return (
        ee.ImageCollection(collection_id)
        .filterDate(to_ee_date(start_date), to_ee_date(end_date))
        .filterBounds(roi.to_ee_geometry())
        .map(_prep)
    )


def get_landsat_collection(
    product_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> ee.ImageCollection:
    """Return the merged, cloud-masked Landsat product collection for *product_key*.

    All four archives are prepared to canonical bands, merged, sorted by
    acquisition time, then the canonical product recipe is applied per image.
    Empty archives (e.g. LT05 in a 2020 window) merge in harmlessly.
    """
    config = get_product("landsat", product_key)
    prepped = [
        _prep_sensor(cid, band_map, roi, start_date, end_date) for _, cid, band_map in _SENSORS
    ]
    merged = prepped[0]
    for collection in prepped[1:]:
        merged = merged.merge(collection)
    ordered = ee.ImageCollection(merged).sort("system:time_start")
    return ordered.map(lambda image: ee.Image(_compute_product(image, config)))
