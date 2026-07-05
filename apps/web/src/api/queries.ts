import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  AppConfig,
  Dataset,
  InspectRequest,
  InspectResult,
  RoiPreset,
  TileResponse,
  TilesRequest,
} from "./types";

export function useCatalog() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: () => apiGet<Dataset[]>("/api/catalog"),
  });
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => apiGet<AppConfig>("/api/config"),
    refetchInterval: 60_000,
  });
}

export function usePresets() {
  return useQuery({
    queryKey: ["presets"],
    queryFn: () => apiGet<RoiPreset[]>("/api/presets/rois"),
    staleTime: Infinity,
  });
}

/** Mint (or re-mint) a tile URL. Not a react-query mutation: layer minting
 *  is driven from the layer store / remint scheduler, not from components. */
export function mintTiles(body: TilesRequest): Promise<TileResponse> {
  return apiPost<TileResponse>("/api/tiles", body);
}

/** Sample the current composite's pixel value at a point (pixel inspector). */
export function inspectPoint(body: InspectRequest): Promise<InspectResult> {
  return apiPost<InspectResult>("/api/inspect", body);
}
