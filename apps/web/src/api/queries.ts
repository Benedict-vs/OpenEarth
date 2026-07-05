import { useQuery } from "@tanstack/react-query";
import { apiGet, apiPost, apiPostBlob } from "./client";
import type {
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
