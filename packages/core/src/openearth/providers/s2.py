"""Earth Engine provider for Sentinel-2 Harmonized spectral data.

v2 changes vs the v1 provider:

- **L2A surface reflectance by default.** Generic products (indices, RGB,
  raw bands) render from ``COPERNICUS/S2_SR_HARMONIZED``; only the methane
  proxies pin L1C TOA via their catalog ``collection_id`` (the point-source
  retrieval literature operates on TOA reflectance).
- **s2cloudless null-guard.** The v1 join assumed every S2 granule had a
  matching cloud-probability image; a missing match crashed tile rendering.
  Scenes without a match are now kept unmasked instead.
- **Missing-scene guard.** The single-scene anomaly raises
  :class:`~openearth.errors.EmptyCollectionError` with a clear message when
  the requested timestamp has no acquisition, instead of failing deep inside
  Earth Engine at render time.
- Products whose catalog entry carries ``builder=`` (``CH4_ANOMALY``) are
  refused by the generic pipeline instead of silently rendering the wrong
  band math.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.catalog.builtin.s2 import S2_COLLECTION_ID, S2_SR_COLLECTION_ID
from openearth.catalog.registry import get_product
from openearth.ee.client import ee_call
from openearth.errors import EmptyCollectionError

if TYPE_CHECKING:
    from openearth.catalog.models import ProductSpec
    from openearth.geometry import ROI

S2_CLOUD_PROB_ID = "COPERNICUS/S2_CLOUD_PROBABILITY"
DEFAULT_CLOUD_PROB_THRESH = 50


def _join_cloud_prob(
    s2: ee.ImageCollection,
    cloud_prob: ee.ImageCollection,
) -> ee.ImageCollection:
    """Join cloud-probability images onto the S2 collection.

    Uses ``ee.Join.saveFirst`` keyed on ``system:index`` (identical in both
    collections for the same granule).
    """
    join = ee.Join.saveFirst(matchKey="cloud_prob_img")
    filt = ee.Filter.equals(leftField="system:index", rightField="system:index")
    return ee.ImageCollection(join.apply(s2, cloud_prob, filt))


def _mask_clouds(
    image: ee.Image,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Mask pixels whose cloud probability exceeds *cloud_prob_thresh*.

    A granule can lack a matching s2cloudless image (the collections are not
    perfectly aligned); such scenes are kept unmasked rather than crashing
    the whole collection.
    """
    prob = image.get("cloud_prob_img")
    return ee.Image(
        ee.Algorithms.If(
            prob,
            image.updateMask(
                ee.Image(prob).select("probability").lt(cloud_prob_thresh),
            ),
            image,
        )
    )


def _to_reflectance(image: ee.Image) -> ee.Image:
    """Scale S2 DN values to [0, 1] reflectance (same 1e4 factor for L1C and L2A)."""
    optical = image.select("B.*").divide(10_000)
    return image.addBands(optical, overwrite=True)


def _compute_index(image: ee.Image, config: ProductSpec) -> ee.Image:
    """Compute a spectral index or select a raw band.

    Preserves ``system:time_start`` so that downstream ``filterDate`` calls
    still work.
    """
    if config.builder is not None:
        raise ValueError(
            f"Product {config.key!r} requires the dedicated builder "
            f"{config.builder!r} and cannot be computed generically."
        )

    if config.is_rgb:
        return image.select(config.bands)

    if config.expression is None and config.key == "CHLA":
        # 4.26 * (B5/B4)^3.94 — requires ee.Image.pow()
        ratio = image.select("B5").divide(image.select("B4"))
        return (
            ratio.pow(3.94)
            .multiply(4.26)
            .rename(config.key)
            .copyProperties(image, ["system:time_start"])
        )

    if config.expression is None:
        return image.select(config.bands).rename(config.key)

    band_map = {b: image.select(b) for b in config.bands or []}
    return (
        image.expression(config.expression, band_map)
        .rename(config.key)
        .copyProperties(image, ["system:time_start"])
    )


def get_s2_base_collection(
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
    collection_id: str = S2_SR_COLLECTION_ID,
) -> ee.ImageCollection:
    """Return cloud-masked, reflectance-scaled S2 collection (all bands).

    Shared preprocessing pipeline for single-index collections, the methane
    anomaly, masking, and source classification. *collection_id* selects
    L2A SR (default) or L1C TOA (``S2_COLLECTION_ID``).
    """
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)
    geometry = roi.to_ee_geometry()

    s2 = (
        ee.ImageCollection(collection_id)
        .filterDate(start, end)
        .filterBounds(geometry)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cloud_max))
    )

    cloud_prob = ee.ImageCollection(S2_CLOUD_PROB_ID).filterDate(start, end).filterBounds(geometry)

    joined = _join_cloud_prob(s2, cloud_prob)
    thresh = cloud_prob_thresh

    return joined.map(lambda img: _mask_clouds(img, thresh)).map(_to_reflectance)


def get_s2_collection(
    index_key: str,
    roi: ROI,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.ImageCollection:
    """Return an S2 index ImageCollection filtered by ROI and dates.

    Cloud masking uses the companion ``COPERNICUS/S2_CLOUD_PROBABILITY``
    collection (s2cloudless): pixels with cloud probability above
    *cloud_prob_thresh* (0-100) are masked out. *cloud_max* is the lenient
    scene-level ``CLOUDY_PIXEL_PERCENTAGE`` pre-filter (default 65 — the
    per-pixel mask handles the actual cloudy pixels; too-low values discard
    most scenes over cloudy climates).
    """
    config = get_product("s2", index_key)
    base = get_s2_base_collection(
        roi,
        start_date,
        end_date,
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
        collection_id=config.collection_id or S2_SR_COLLECTION_ID,
    )
    return base.map(lambda img: _compute_index(img, config))


def _b12_over_b11(img: ee.Image) -> ee.Image:
    return img.select("B12").divide(img.select("B11")).copyProperties(img, ["system:time_start"])


def compute_methane_anomaly(
    roi: ROI,
    target_date: str | date | datetime,
    half_window_days: int,
    ref_start: str | date | datetime,
    ref_end: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Methane anomaly: target-window B12/B11 mean minus reference-period mean.

    Returns a single-band ``ee.Image`` named ``CH4_ANOMALY``. Negative values
    indicate stronger SWIR absorption in the target relative to the reference
    period. Runs on L1C TOA (retrieval-literature convention).

    NOTE: this is the v1 uncalibrated browsing proxy, kept as the map
    "quicklook" layer. The calibrated MBSP/MBMP retrieval replaces it for
    quantitative work in Phase 3.
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    t_start = target_date - timedelta(days=half_window_days)
    t_end = target_date + timedelta(days=half_window_days + 1)

    ref_base = get_s2_base_collection(
        roi,
        ref_start,
        ref_end,
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
        collection_id=S2_COLLECTION_ID,
    )
    ref_ratio = ref_base.map(_b12_over_b11).mean()

    target_base = get_s2_base_collection(
        roi,
        t_start.isoformat(),
        t_end.isoformat(),
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
        collection_id=S2_COLLECTION_ID,
    )
    target_ratio = target_base.map(_b12_over_b11).mean()

    return target_ratio.subtract(ref_ratio).rename("CH4_ANOMALY")


def compute_methane_anomaly_single_scene(
    roi: ROI,
    timestamp_ms: int,
    ref_start: str | date | datetime,
    ref_end: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Methane anomaly from a single S2 scene vs reference mean.

    Like :func:`compute_methane_anomaly` but the target is one acquisition
    identified by *timestamp_ms*. Raises :class:`EmptyCollectionError` when
    no scene exists at that timestamp (v1 failed opaquely at render time).
    """
    centre = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    t_start = centre - timedelta(hours=12)
    t_end = centre + timedelta(hours=12)

    ref_base = get_s2_base_collection(
        roi,
        ref_start,
        ref_end,
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
        collection_id=S2_COLLECTION_ID,
    )
    ref_ratio = ref_base.map(_b12_over_b11).mean()

    target_base = get_s2_base_collection(
        roi,
        t_start.isoformat(),
        t_end.isoformat(),
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
        collection_id=S2_COLLECTION_ID,
    )
    matches = target_base.filter(ee.Filter.eq("system:time_start", timestamp_ms))
    n_matches = int(ee_call(matches.size().getInfo) or 0)
    if n_matches == 0:
        raise EmptyCollectionError(
            f"No Sentinel-2 scene found at timestamp {timestamp_ms} "
            f"({centre.isoformat()}) for this ROI — it may have been dropped "
            f"by the cloud filters. Pick a different acquisition."
        )
    target_ratio = _b12_over_b11(matches.first())

    return ee.Image(target_ratio).subtract(ref_ratio).rename("CH4_ANOMALY")
