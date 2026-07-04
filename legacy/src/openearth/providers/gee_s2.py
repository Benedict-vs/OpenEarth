"""Earth Engine provider for Sentinel-2 Harmonized spectral data."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import ee

from openearth.analytics.conversions import to_ee_date
from openearth.providers.s2_registry import (
    S2_COLLECTION_ID,
    get_s2_index_config,
)

S2_CLOUD_PROB_ID = "COPERNICUS/S2_CLOUD_PROBABILITY"
DEFAULT_CLOUD_PROB_THRESH = 50


def _join_cloud_prob(
    s2: ee.ImageCollection,
    cloud_prob: ee.ImageCollection,
) -> ee.ImageCollection:
    """Join cloud-probability images onto the S2 collection.

    Uses ``ee.Join.saveFirst`` keyed on ``system:index``
    (identical in both collections for the same granule).
    """
    join = ee.Join.saveFirst(
        matchKey="cloud_prob_img",
    )
    filt = ee.Filter.equals(
        leftField="system:index",
        rightField="system:index",
    )
    return ee.ImageCollection(
        join.apply(s2, cloud_prob, filt),
    )


def _mask_clouds(
    image: ee.Image,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Mask pixels whose cloud probability exceeds *cloud_prob_thresh*.

    Follows the official GEE S2_CLOUD_PROBABILITY example:
    select the ``probability`` band from the joined image
    and mask pixels above the threshold.
    """
    clouds = ee.Image(
        image.get("cloud_prob_img"),
    ).select("probability")
    is_clear = clouds.lt(cloud_prob_thresh)
    return image.updateMask(is_clear)


def _to_reflectance(
    image: ee.Image,
) -> ee.Image:
    """Scale S2 L1C DN values to [0, 1] reflectance."""
    optical = image.select("B.*").divide(10_000)
    return image.addBands(optical, overwrite=True)


def _compute_index(
    image: ee.Image, config,
) -> ee.Image:
    """Compute a spectral index or select a raw band.

    Preserves ``system:time_start`` so that downstream
    ``filterDate`` calls still work.
    """
    if getattr(config, "is_rgb", False):
        return image.select(config.bands)

    if config.expression is None and config.key == "CHLA":
        # 4.26 * (B5/B4)^3.94 — requires ee.Image.pow()
        ratio = image.select("B5").divide(
            image.select("B4"),
        )
        return (
            ratio.pow(3.94)
            .multiply(4.26)
            .rename(config.key)
            .copyProperties(
                image, ["system:time_start"],
            )
        )

    if config.expression is None:
        return (
            image.select(config.bands)
            .rename(config.key)
        )

    band_map = {
        b: image.select(b) for b in config.bands
    }
    return (
        image.expression(
            config.expression, band_map,
        )
        .rename(config.key)
        .copyProperties(
            image, ["system:time_start"],
        )
    )


def _get_s2_base_collection(
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
    collection_id: str | None = None,
) -> ee.ImageCollection:
    """Return cloud-masked, reflectance-scaled S2 collection (all bands).

    This is the shared preprocessing pipeline used by both
    ``get_s2_collection`` (single-index) and
    ``compute_methane_anomaly`` (multi-band anomaly).
    """
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    cid = collection_id or S2_COLLECTION_ID
    s2 = (
        ee.ImageCollection(cid)
        .filterDate(start, end)
        .filterBounds(geometry)
        .filter(
            ee.Filter.lte(
                "CLOUDY_PIXEL_PERCENTAGE",
                cloud_max,
            ),
        )
    )

    cloud_prob = (
        ee.ImageCollection(S2_CLOUD_PROB_ID)
        .filterDate(start, end)
        .filterBounds(geometry)
    )

    joined = _join_cloud_prob(s2, cloud_prob)
    thresh = cloud_prob_thresh

    return (
        joined
        .map(lambda img: _mask_clouds(img, thresh))
        .map(_to_reflectance)
    )


def get_s2_collection(
    index_key: str,
    geometry: ee.Geometry,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.ImageCollection:
    """Return an S2 index ImageCollection filtered by ROI and dates.

    Cloud masking uses the companion
    ``COPERNICUS/S2_CLOUD_PROBABILITY`` collection
    (s2cloudless).  Pixels with a cloud probability
    above *cloud_prob_thresh* (0-100) are masked out.

    Parameters
    ----------
    index_key:
        Registry key, e.g. ``"NDVI"``, ``"B11"``.
    geometry:
        Earth Engine geometry for spatial filtering.
    start_date, end_date:
        Temporal window (inclusive start, exclusive
        end).
    cloud_max:
        Maximum ``CLOUDY_PIXEL_PERCENTAGE`` metadata
        value (0-100).  Images above this threshold
        are dropped *before* per-pixel cloud masking.
        Default is 65 — lenient, because the per-pixel
        s2cloudless mask handles the actual cloudy
        pixels.  Too-low values (e.g. 20) discard
        most scenes over cloudy climates.
    cloud_prob_thresh:
        Per-pixel cloud probability threshold (0-100).
        Pixels above this value are masked.
    """
    config = get_s2_index_config(index_key)
    base = _get_s2_base_collection(
        geometry, start_date, end_date,
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
        collection_id=getattr(
            config, "collection_id", None,
        ),
    )
    return base.map(
        lambda img: _compute_index(img, config),
    )


def compute_methane_anomaly(
    geometry: ee.Geometry,
    target_date: str | date | datetime,
    half_window_days: int,
    ref_start: str | date | datetime,
    ref_end: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Compute methane anomaly: target B12/B11 minus reference B12/B11.

    Returns a single-band ``ee.Image`` named ``CH4_ANOMALY``.
    Negative values indicate stronger methane absorption in the
    target relative to the reference period mean.

    Parameters
    ----------
    geometry:
        Earth Engine geometry for spatial filtering.
    target_date:
        Centre date for the target observation window.
    half_window_days:
        Days before/after *target_date* to composite.
    ref_start, ref_end:
        Reference (baseline) period for the mean B12/B11
        ratio that is subtracted from the target.
    cloud_max, cloud_prob_thresh:
        Cloud-filtering parameters (see ``get_s2_collection``).
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    from datetime import timedelta

    t_start = target_date - timedelta(
        days=half_window_days,
    )
    t_end = target_date + timedelta(
        days=half_window_days + 1,
    )

    def _b12_over_b11(img: ee.Image) -> ee.Image:
        return (
            img.select("B12")
            .divide(img.select("B11"))
            .copyProperties(img, ["system:time_start"])
        )

    # Reference: mean B12/B11 over the full baseline period.
    ref_base = _get_s2_base_collection(
        geometry, ref_start, ref_end,
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
    )
    ref_ratio = ref_base.map(_b12_over_b11).mean()

    # Target: mean B12/B11 over the short target window.
    target_base = _get_s2_base_collection(
        geometry, t_start.isoformat(), t_end.isoformat(),
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
    )
    target_ratio = target_base.map(_b12_over_b11).mean()

    return (
        target_ratio
        .subtract(ref_ratio)
        .rename("CH4_ANOMALY")
    )


def compute_methane_anomaly_single_scene(
    geometry: ee.Geometry,
    timestamp_ms: int,
    ref_start: str | date | datetime,
    ref_end: str | date | datetime,
    *,
    cloud_max: int = 65,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Methane anomaly from a single S2 scene vs reference mean.

    Like :func:`compute_methane_anomaly` but the target is one
    acquisition identified by *timestamp_ms* instead of a
    date-window composite.
    """
    centre = datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc,
    )
    t_start = centre - timedelta(hours=12)
    t_end = centre + timedelta(hours=12)

    def _b12_over_b11(img: ee.Image) -> ee.Image:
        return (
            img.select("B12")
            .divide(img.select("B11"))
            .copyProperties(img, ["system:time_start"])
        )

    ref_base = _get_s2_base_collection(
        geometry, ref_start, ref_end,
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
    )
    ref_ratio = ref_base.map(_b12_over_b11).mean()

    target_base = _get_s2_base_collection(
        geometry,
        t_start.isoformat(),
        t_end.isoformat(),
        cloud_max=cloud_max,
        cloud_prob_thresh=cloud_prob_thresh,
    )
    target_img = (
        target_base
        .filter(
            ee.Filter.eq(
                "system:time_start", timestamp_ms,
            ),
        )
        .first()
    )
    target_ratio = _b12_over_b11(target_img)

    return (
        target_ratio
        .subtract(ref_ratio)
        .rename("CH4_ANOMALY")
    )
