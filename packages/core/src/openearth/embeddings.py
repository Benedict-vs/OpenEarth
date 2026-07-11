"""AlphaEarth Satellite Embedding operations (similarity / change / clusters).

Google DeepMind's *AlphaEarth Foundations* embedding
(``GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL``) gives one **64-band, unit-norm** vector
per 10 m pixel per year (2017–present, one image per UTM zone). Because the vectors
are unit-norm, the **dot product is cosine similarity** — no normalization needed —
and year-to-year change is ``1 − dot``. This module is the EE-facing half; the pure
constants (band list, vis ranges, palettes) are importable without a live session.

Not methane science, so it lives at the package top level. Attribution (CC-BY 4.0)
is mandatory wherever a layer is shown — see :data:`ATTRIBUTION`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import ee

from openearth.ee.client import ee_call
from openearth.errors import EmptyCollectionError

if TYPE_CHECKING:
    from openearth.geometry import BBox

COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
BANDS: list[str] = [f"A{i:02d}" for i in range(64)]

# Mandatory attribution (CC-BY 4.0), verbatim, shown in the catalog + the view footer.
ATTRIBUTION = (
    "The AlphaEarth Foundations Satellite Embedding dataset is produced by "
    "Google and Google DeepMind."
)

# ── Visualization (pure constants; the API builds legends from these) ─────────

# Similarity is cosine ∈ [-1, 1]; negatives are meaningful ("actively dissimilar").
# The quicklook clamps to [-0.2, 1.0] (a diverging blue→white→red ramp) — the legend
# must label the low-end clamp.
SIMILARITY_VIS: tuple[float, float] = (-0.2, 1.0)
SIMILARITY_PALETTE: list[str] = [
    "#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7",
    "#fddbc7", "#f4a582", "#d6604d", "#b2182b", "#67001f",
]  # fmt: skip

# Change = 1 − cosine ∈ [0, 2]; antipodal cases > 1 are rare, so clamp the ramp to [0, 1].
CHANGE_VIS: tuple[float, float] = (0.0, 1.0)
CHANGE_PALETTE: list[str] = [
    "#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60",
    "#cf4446", "#ed6925", "#fb9b06", "#f7d13d", "#fcffa4",
]  # fmt: skip

# Qualitative palette cycled to k; cluster indices are arbitrary integers, never
# class semantics — the legend maps index → color, nothing more.
CLUSTER_PALETTE: list[str] = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffff33",
    "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62", "#8da0cb",
]  # fmt: skip

K_MIN, K_MAX = 2, 12

_years_cache: list[int] | None = None


def available_years() -> list[int]:
    """Distinct years present in the collection, probed once and cached.

    The upstream commits to ongoing annual layers, so the newest year is derived
    from the live collection rather than hardcoded (2025 is live as of writing).
    """
    global _years_cache
    if _years_cache is None:
        times = (
            ee_call(ee.ImageCollection(COLLECTION).aggregate_array("system:time_start").getInfo)
            or []
        )
        _years_cache = sorted({datetime.fromtimestamp(t / 1000, tz=UTC).year for t in times})
    return _years_cache


def year_mosaic(year: int) -> ee.Image:
    """Single 64-band embedding image for *year* (mosaic across UTM-zone images)."""
    start = ee.Date.fromYMD(year, 1, 1)
    return (
        ee.ImageCollection(COLLECTION)
        .filterDate(start, start.advance(1, "year"))
        .mosaic()
        .select(BANDS)
    )


def seed_vector(lat: float, lon: float, year: int) -> list[float]:
    """The 64-D embedding at a point, sampled at native 10 m.

    Raises :class:`EmptyCollectionError` if the location has no embedding for the
    year (masked / outside coverage) — the caller surfaces that as a clear error
    rather than minting a similarity layer against a partial seed.
    """
    point = ee.Geometry.Point([lon, lat])
    values = (
        ee_call(
            year_mosaic(year)
            .reduceRegion(reducer=ee.Reducer.first(), geometry=point, scale=10)
            .getInfo
        )
        or {}
    )
    seed = [values.get(b) for b in BANDS]
    if any(v is None for v in seed):
        raise EmptyCollectionError(
            f"No AlphaEarth embedding at ({lat:.5f}, {lon:.5f}) for {year} "
            "(outside coverage or masked)."
        )
    return [float(v) for v in seed if v is not None]


def similarity_image(seed: list[float], year: int) -> ee.Image:
    """Cosine similarity of every *year* pixel to *seed* — a single band in [-1, 1]."""
    seed_img = ee.Image.constant(seed).rename(BANDS)
    return year_mosaic(year).multiply(seed_img).reduce(ee.Reducer.sum()).rename("similarity")


def change_image(year_a: int, year_b: int) -> ee.Image:
    """Embedding change ``1 − cos(a, b)`` per pixel — a single band in [0, 2]."""
    dot = year_mosaic(year_a).multiply(year_mosaic(year_b)).reduce(ee.Reducer.sum())
    return ee.Image.constant(1).subtract(dot).rename("change")


def cluster_image(
    bbox: BBox, year: int, k: int, *, n_samples: int = 5000, seed: int = 0
) -> ee.Image:
    """Unsupervised k-means over the *year* embedding within *bbox*.

    The clusterer is trained on ``n_samples`` pixels sampled inside *bbox*; both the
    sampling and ``wekaKMeans`` seeds are pinned so a tile re-mint (after the ~4 h
    getMapId expiry) re-clusters identically. Output band ``cluster`` holds the
    integer class index (0…k−1) — an arbitrary label, never a semantic class.
    """
    mosaic = year_mosaic(year)
    region = bbox.to_ee_geometry()
    training = mosaic.sample(region=region, scale=10, numPixels=n_samples, seed=seed)
    clusterer = ee.Clusterer.wekaKMeans(k, seed=seed).train(training)
    return mosaic.cluster(clusterer).rename("cluster")
