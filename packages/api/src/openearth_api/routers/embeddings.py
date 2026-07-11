"""Embeddings Explorer endpoints (AlphaEarth similarity / change / clusters).

All routes touch Earth Engine (mosaic, sample, mint) so they sit behind
``ensure_ee``; the year probe + seed vectors are cached, tile URLs are not.
"""

from __future__ import annotations

from typing import Annotated

import diskcache
from fastapi import APIRouter, Depends

from openearth_api.deps import ensure_ee, get_cache
from openearth_api.schemas import (
    EmbeddingChangeRequest,
    EmbeddingClusterRequest,
    EmbeddingSimilarityRequest,
    EmbeddingTileOut,
    EmbeddingYearsOut,
)
from openearth_api.services import embeddings as svc

router = APIRouter(tags=["embeddings"], dependencies=[Depends(ensure_ee)])

CacheDep = Annotated[diskcache.Cache, Depends(get_cache)]


@router.get("/embeddings/years")
def embedding_years() -> EmbeddingYearsOut:
    return svc.list_years()


@router.post("/embeddings/similarity")
def embedding_similarity(body: EmbeddingSimilarityRequest, cache: CacheDep) -> EmbeddingTileOut:
    return svc.similarity(body, cache)


@router.post("/embeddings/change")
def embedding_change(body: EmbeddingChangeRequest, cache: CacheDep) -> EmbeddingTileOut:
    return svc.change(body, cache)


@router.post("/embeddings/cluster")
def embedding_cluster(body: EmbeddingClusterRequest, cache: CacheDep) -> EmbeddingTileOut:
    return svc.cluster(body, cache)
