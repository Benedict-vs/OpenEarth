"""Earth Engine provider for Sentinel-2 Harmonized spectral data."""

from __future__ import annotations

from datetime import date, datetime

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
    This is the recommended Earth Engine pattern — it
    avoids nested aggregations inside ``.map()`` which
    can silently break server-side computation.
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


def _add_cloud_band(image: ee.Image) -> ee.Image:
    """Promote the joined cloud-prob image to a band."""
    cloud_img = ee.Image(
        image.get("cloud_prob_img"),
    )
    return image.addBands(
        cloud_img.rename("cloud_prob"),
    )


def _mask_clouds(
    image: ee.Image,
    cloud_prob_thresh: int = DEFAULT_CLOUD_PROB_THRESH,
) -> ee.Image:
    """Mask pixels whose cloud probability exceeds *cloud_prob_thresh*."""
    mask = (
        image.select("cloud_prob")
        .lt(cloud_prob_thresh)
    )
    return image.updateMask(mask)


def _to_reflectance(
    image: ee.Image,
) -> ee.Image:
    """Scale S2 L1C DN values to [0, 1] reflectance.

    Only optical bands (prefixed ``B``) are divided;
    the ``cloud_prob`` band is left untouched.
    """
    optical = image.select("B.*").divide(10_000)
    return image.addBands(optical, overwrite=True)


def _compute_index(
    image: ee.Image, config,
) -> ee.Image:
    """Compute a spectral index or select a raw band."""
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
    start = to_ee_date(start_date)
    end = to_ee_date(end_date)

    s2 = (
        ee.ImageCollection(S2_COLLECTION_ID)
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

    # Server-side join — no nested aggregations.
    joined = _join_cloud_prob(s2, cloud_prob)

    thresh = cloud_prob_thresh

    return (
        joined
        .map(_add_cloud_band)
        .map(lambda img: _mask_clouds(img, thresh))
        .map(_to_reflectance)
        .map(lambda img: _compute_index(img, config))
    )
