"""Embeddings Explorer services: mint similarity / change / cluster tile layers.

Thin over ``openearth.embeddings`` (the EE half) — validates the requested year
against the live collection, clamps k, seeds the clusterer, and mints a tile URL
with a legend. Seed vectors are cached (``cache_key("embed_seed", …)``); tile URLs
are never cached (they expire, and a cluster re-mint retrains with the pinned seed).
The core fns are imported by name so offline tests fake them at this module level.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from fastapi import HTTPException

from openearth.catalog.models import ProductSpec
from openearth.ee.render import mint_tile_url
from openearth.embeddings import (
    ATTRIBUTION,
    CHANGE_PALETTE,
    CHANGE_VIS,
    CLUSTER_PALETTE,
    K_MAX,
    K_MIN,
    SIMILARITY_PALETTE,
    SIMILARITY_VIS,
    available_years,
    change_image,
    cluster_image,
    seed_vector,
    similarity_image,
)
from openearth_api.cache import cache_key
from openearth_api.schemas import (
    EmbeddingChangeRequest,
    EmbeddingClusterRequest,
    EmbeddingSimilarityRequest,
    EmbeddingTileOut,
    EmbeddingYearsOut,
    LegendOut,
)

if TYPE_CHECKING:
    import diskcache


def _validate_year(year: int) -> None:
    years = available_years()
    if year not in years:
        raise HTTPException(
            422,
            f"Year {year} unavailable — AlphaEarth covers {years[0]}–{years[-1]}.",
        )


def _spec(
    key: str, vis: tuple[float, float], palette: list[str], unit: str, desc: str
) -> ProductSpec:
    """A minimal vis-carrying ProductSpec so mint_tile_url renders embeddings layers."""
    return ProductSpec(
        key=key,
        name=key,
        vis_min=vis[0],
        vis_max=vis[1],
        valid_min=vis[0],
        valid_max=vis[1],
        display_unit=unit,
        palette=palette,
        description=desc,
    )


def _legend(spec: ProductSpec) -> LegendOut:
    return LegendOut(
        min=spec.vis_min,
        max=spec.vis_max,
        unit=spec.display_unit,
        palette=list(spec.palette),
        display_scale=spec.display_scale,
        is_rgb=False,
        description=spec.description,
    )


def list_years() -> EmbeddingYearsOut:
    return EmbeddingYearsOut(years=available_years())


def similarity(req: EmbeddingSimilarityRequest, cache: diskcache.Cache) -> EmbeddingTileOut:
    _validate_year(req.year)
    key = cache_key("embed_seed", lat=round(req.lat, 5), lon=round(req.lon, 5), year=req.year)
    seed = cache.get(key)
    if seed is None:
        seed = seed_vector(req.lat, req.lon, req.year)  # EmptyCollectionError → 422 via handler
        cache.set(key, seed)
    image = similarity_image(list(seed), req.year)
    spec = _spec(
        "similarity",
        SIMILARITY_VIS,
        SIMILARITY_PALETTE,
        "cosine similarity",
        "Cosine similarity to the seed embedding. Blue = dissimilar (values below "
        f"{SIMILARITY_VIS[0]} clamp to the low end), red = near-identical (1.0).",
    )
    ref = mint_tile_url(image, spec, attribution=ATTRIBUTION)
    return EmbeddingTileOut(
        tile_url=ref.url,
        expires_at=ref.expires_at,
        attribution=ref.attribution,
        legend=_legend(spec),
        seed_norm=math.sqrt(sum(x * x for x in seed)),
    )


def change(req: EmbeddingChangeRequest, cache: diskcache.Cache) -> EmbeddingTileOut:
    for year in (req.year_a, req.year_b):
        _validate_year(year)
    image = change_image(req.year_a, req.year_b)
    spec = _spec(
        "change",
        CHANGE_VIS,
        CHANGE_PALETTE,
        "1 − cosine",
        f"Embedding change between {req.year_a} and {req.year_b} (1 − cosine). "
        "Bright = strongly changed surface; dark = stable.",
    )
    ref = mint_tile_url(image, spec, attribution=ATTRIBUTION)
    return EmbeddingTileOut(
        tile_url=ref.url,
        expires_at=ref.expires_at,
        attribution=ref.attribution,
        legend=_legend(spec),
    )


def cluster(req: EmbeddingClusterRequest, cache: diskcache.Cache) -> EmbeddingTileOut:
    _validate_year(req.year)
    k = max(K_MIN, min(K_MAX, req.k))
    bbox = req.roi.to_domain()
    image = cluster_image(bbox, req.year, k)
    palette = CLUSTER_PALETTE[:k]
    spec = _spec(
        "cluster",
        (0.0, float(k - 1)),
        palette,
        "cluster index",
        f"{k} unsupervised k-means clusters of the {req.year} embedding. Colors map "
        "index → hue only; the integer labels carry no class semantics.",
    )
    ref = mint_tile_url(image, spec, attribution=ATTRIBUTION)
    return EmbeddingTileOut(
        tile_url=ref.url,
        expires_at=ref.expires_at,
        attribution=ref.attribution,
        legend=_legend(spec),
        n_clusters=k,
    )
