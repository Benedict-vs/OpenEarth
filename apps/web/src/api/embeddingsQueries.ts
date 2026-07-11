/** Plain calls + a hook for the Embeddings Explorer (AlphaEarth) API. */
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  EmbeddingChangeRequest,
  EmbeddingClusterRequest,
  EmbeddingSimilarityRequest,
  EmbeddingTile,
  EmbeddingYears,
} from "./types";

/** Available AlphaEarth years, probed from the live collection (cached server-side). */
export function useEmbeddingYears(enabled: boolean) {
  return useQuery({
    queryKey: ["embeddings", "years"],
    enabled,
    staleTime: Infinity,
    queryFn: () => apiGet<EmbeddingYears>("/api/embeddings/years"),
  });
}

export function mintSimilarity(body: EmbeddingSimilarityRequest): Promise<EmbeddingTile> {
  return apiPost<EmbeddingTile>("/api/embeddings/similarity", body);
}

export function mintChange(body: EmbeddingChangeRequest): Promise<EmbeddingTile> {
  return apiPost<EmbeddingTile>("/api/embeddings/change", body);
}

export function mintCluster(body: EmbeddingClusterRequest): Promise<EmbeddingTile> {
  return apiPost<EmbeddingTile>("/api/embeddings/cluster", body);
}
