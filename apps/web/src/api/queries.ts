import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPost, apiPostBlob, apiPut } from "./client";
import type {
  Aoi,
  AoiIn,
  AppConfig,
  Dataset,
  ExportGeotiffRequest,
  InspectRequest,
  InspectResult,
  JobCreated,
  RoiPreset,
  ThumbnailRequest,
  TileResponse,
  TilesRequest,
  WindField,
  Workspace,
  WorkspaceIn,
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

/** Submit a GeoTIFF export; the returned job streams window progress over SSE. */
export function submitGeotiffExport(body: ExportGeotiffRequest): Promise<JobCreated> {
  return apiPost<JobCreated>("/api/export/geotiff", body);
}

/** Render the composite to a PNG for download (synchronous). */
export function exportPngBlob(body: ThumbnailRequest): Promise<Blob> {
  return apiPostBlob("/api/export/png", body);
}

export interface WindFieldParams {
  west: number;
  south: number;
  east: number;
  north: number;
  /** Sample instant, ISO 8601 (e.g. "2024-07-15T12:00:00Z"). */
  time: string;
  /** Columns; the server derives rows from the box aspect ratio. */
  nx?: number;
}

/** Gridded ERA5 10 m wind over a viewport box at an instant (for the overlay). */
export function fetchWindField(params: WindFieldParams): Promise<WindField> {
  const query = new URLSearchParams({
    west: String(params.west),
    south: String(params.south),
    east: String(params.east),
    north: String(params.north),
    time: params.time,
  });
  if (params.nx !== undefined) query.set("nx", String(params.nx));
  return apiGet<WindField>(`/api/wind/field?${query.toString()}`);
}

// ── Saved AOIs ─────────────────────────────────────────────
const AOIS_KEY = ["aois"] as const;

export function useAois() {
  return useQuery({ queryKey: AOIS_KEY, queryFn: () => apiGet<Aoi[]>("/api/aois") });
}

export function useSaveAoi() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AoiIn) => apiPost<Aoi>("/api/aois", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: AOIS_KEY }),
  });
}

export function useDeleteAoi() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/api/aois/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: AOIS_KEY }),
  });
}

// ── Workspaces ─────────────────────────────────────────────
const WORKSPACES_KEY = ["workspaces"] as const;

export function useWorkspaces() {
  return useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: () => apiGet<Workspace[]>("/api/workspaces"),
  });
}

export function useSaveWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkspaceIn) => apiPost<Workspace>("/api/workspaces", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: WORKSPACES_KEY }),
  });
}

export function useUpdateWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: WorkspaceIn }) =>
      apiPut<Workspace>(`/api/workspaces/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: WORKSPACES_KEY }),
  });
}

export function useDeleteWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/api/workspaces/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: WORKSPACES_KEY }),
  });
}
