/** React-query hooks + plain calls for the Methane Lab API. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch, apiPost, apiPostForm } from "./client";
import type {
  AnalyzeRequest,
  BBoxIn,
  Detection,
  DetectionDetail,
  DetectionPatch,
  EmitPlumes,
  JobCreated,
  MlScanRequest,
  MlStatus,
  ReferenceEvent,
  SceneInfo,
  ScreeningRequest,
  Site,
  SiteIn,
  SitePatch,
  Validation,
  ValidationImport,
} from "./types";

const SITES_KEY = ["methane", "sites"] as const;
const EVENTS_KEY = ["methane", "events"] as const;
const detectionsKey = (siteId: number | null, source: string | null) =>
  ["methane", "detections", siteId, source ?? "all"] as const;

// ── Sites ──

export function useSites() {
  return useQuery({ queryKey: SITES_KEY, queryFn: () => apiGet<Site[]>("/api/methane/sites") });
}

export function useCreateSite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SiteIn) => apiPost<Site>("/api/methane/sites", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: SITES_KEY }),
  });
}

export function usePatchSite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: SitePatch }) =>
      apiPatch<Site>(`/api/methane/sites/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: SITES_KEY }),
  });
}

export function useDeleteSite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiDelete(`/api/methane/sites/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: SITES_KEY }),
  });
}

// ── Scenes ──

export function useSiteScenes(
  siteId: number | null,
  start: string,
  end: string,
  maxCloud = 80,
  /** Scenes are listed over the analysis area (when set), not the browse-scale
   *  site ROI, so every listed scene covers the chip actually analyzed. */
  roi?: BBoxIn | null,
) {
  return useQuery({
    queryKey: ["methane", "scenes", siteId, start, end, maxCloud, roi ?? null],
    enabled: siteId != null,
    queryFn: () => {
      const q = new URLSearchParams({ start, end, max_cloud: String(maxCloud) });
      if (roi) {
        q.set("west", String(roi.west));
        q.set("south", String(roi.south));
        q.set("east", String(roi.east));
        q.set("north", String(roi.north));
      }
      return apiGet<SceneInfo[]>(`/api/methane/sites/${siteId}/scenes?${q.toString()}`);
    },
  });
}

// ── Analyze / screening (jobs) ──

export function submitAnalyze(body: AnalyzeRequest): Promise<JobCreated> {
  return apiPost<JobCreated>("/api/methane/analyze", body);
}

export function submitScreening(body: ScreeningRequest): Promise<JobCreated> {
  return apiPost<JobCreated>("/api/methane/screening", body);
}

// ── ML scan (candidate ranker; never an autonomous detector) ──

export function submitMlScan(body: MlScanRequest): Promise<JobCreated> {
  return apiPost<JobCreated>("/api/methane/ml/scan", body);
}

export function useMlStatus() {
  return useQuery({
    queryKey: ["methane", "ml", "status"],
    queryFn: () => apiGet<MlStatus>("/api/methane/ml/status"),
    staleTime: 60_000,
  });
}

// ── Detections ──

/** Feed rows for a site, optionally filtered by source ("physics" | "ml"). */
export function useDetections(siteId: number | null, source: string | null = null) {
  return useQuery({
    queryKey: detectionsKey(siteId, source),
    queryFn: () => {
      const q = new URLSearchParams();
      if (siteId != null) q.set("site_id", String(siteId));
      if (source) q.set("source", source);
      const qs = q.toString();
      return apiGet<Detection[]>(`/api/methane/detections${qs ? `?${qs}` : ""}`);
    },
  });
}

export function useDetectionDetail(detId: string | null) {
  return useQuery({
    queryKey: ["methane", "detection", detId],
    enabled: detId != null,
    queryFn: () => apiGet<DetectionDetail>(`/api/methane/detections/${detId}`),
  });
}

export function usePatchDetection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DetectionPatch }) =>
      apiPatch<DetectionDetail>(`/api/methane/detections/${id}`, body),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ["methane", "detections"] });
      void qc.invalidateQueries({ queryKey: ["methane", "detection", vars.id] });
    },
  });
}

export function useDeleteDetection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete(`/api/methane/detections/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["methane", "detections"] }),
  });
}

export function overlayUrl(detId: string, vmax?: number): string {
  const q = vmax != null ? `?vmax=${vmax}` : "";
  return `/api/methane/detections/${detId}/overlay.png${q}`;
}

// ── Validation ──

export function useValidationEvents() {
  return useQuery({
    queryKey: EVENTS_KEY,
    queryFn: () => apiGet<ReferenceEvent[]>("/api/methane/validation/events"),
  });
}

export function useImportValidation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, source, fmt }: { file: File; source: string; fmt: "csv" | "geojson" }) => {
      const form = new FormData();
      form.append("file", file);
      form.append("source", source);
      form.append("fmt", fmt);
      return apiPostForm<ValidationImport>("/api/methane/validation/import", form);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: EVENTS_KEY }),
  });
}

export function useValidateDetection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiPost<Validation>(`/api/methane/detections/${id}/validate`, {}),
    onSuccess: (_data, id) => qc.invalidateQueries({ queryKey: ["methane", "detection", id] }),
  });
}

// ── EMIT plumes (Phase 6) ──

/** EMIT plume complexes over a site's bbox + date window (GEE V001 and/or V002). */
export function useEmitPlumes(site: Site | null, start: string, end: string, enabled: boolean) {
  return useQuery({
    queryKey: ["methane", "emit", "plumes", site?.id ?? null, start, end],
    enabled: enabled && site != null,
    staleTime: 5 * 60_000,
    queryFn: () => {
      const { west, south, east, north } = site!.bbox;
      const q = new URLSearchParams({
        west: String(west),
        south: String(south),
        east: String(east),
        north: String(north),
        start,
        end,
      });
      return apiGet<EmitPlumes>(`/api/methane/emit/plumes?${q.toString()}`);
    },
  });
}

/** Cross-match a detection against EMIT plumes; writes its `emit_json` evidence. */
export function useEmitMatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiPost<DetectionDetail>(`/api/methane/detections/${id}/emit-match`, {}),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: ["methane", "detection", id] });
      void qc.invalidateQueries({ queryKey: ["methane", "detections"] });
    },
  });
}
