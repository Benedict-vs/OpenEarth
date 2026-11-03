/** React-query hooks + plain calls for the Timelapse Studio API. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch, apiPost, apiPostBlob } from "./client";
import type {
  Preflight,
  PreflightRequest,
  Render,
  RenderDetail,
  ThumbnailRequest,
  TimelapseCreated,
  TimelapseRequest,
} from "./types";

const RENDERS_KEY = ["timelapse", "renders"] as const;

export function useRenders() {
  return useQuery({
    queryKey: RENDERS_KEY,
    queryFn: () => apiGet<Render[]>("/api/timelapse"),
    // Poll while any render is still running so the gallery flips to succeeded.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r) => r.status === "running") ? 2000 : false,
  });
}

export function useRenderDetail(renderId: string | null) {
  return useQuery({
    queryKey: ["timelapse", "render", renderId],
    enabled: renderId != null,
    queryFn: () => apiGet<RenderDetail>(`/api/timelapse/${renderId}`),
  });
}

export function submitTimelapse(body: TimelapseRequest): Promise<TimelapseCreated> {
  return apiPost<TimelapseCreated>("/api/timelapse", body);
}

/**
 * Availability probe (decision 11): per-window scene counts + the native cap.
 * Cheap (collection aggregates only) and cached hard server-side, so the strip
 * refreshes as the Area/Span settle. `null` request disables the query.
 */
export function usePreflight(body: PreflightRequest | null) {
  return useQuery({
    queryKey: ["timelapse", "preflight", body],
    enabled: body != null,
    // The result is a live EE call; keep it warm so re-selecting a render or
    // toggling knobs that don't affect availability doesn't re-probe.
    staleTime: 5 * 60_000,
    queryFn: () => apiPost<Preflight>("/api/timelapse/preflight", body),
  });
}

/** Mint one representative preview still (mean composite over `body`'s window). */
export function fetchPreview(body: ThumbnailRequest): Promise<Blob> {
  return apiPostBlob("/api/thumbnail", body);
}

/** Cooperatively stop a running render job (frames rendered so far are kept). */
export function cancelJob(jobId: string): Promise<unknown> {
  return apiDelete(`/api/jobs/${jobId}`);
}

export function useRenameRender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      apiPatch<Render>(`/api/timelapse/${id}`, { title }),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: RENDERS_KEY });
      void qc.invalidateQueries({ queryKey: ["timelapse", "render", id] });
    },
  });
}

export function useDeleteRender() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDelete(`/api/timelapse/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: RENDERS_KEY }),
  });
}

/** URL of a rendered frame PNG (immutable; long-cached by the server). */
export function frameUrl(renderId: string, index: number): string {
  return `/api/timelapse/${renderId}/frames/${index}`;
}

/** URL of one frame as a full-resolution still download (attachment). */
export function stillUrl(renderId: string, index: number): string {
  return `/api/timelapse/${renderId}/still/${index}`;
}

/** URL of the encoded movie download, optionally a crop variant ("1:1" | "9:16"). */
export function downloadUrl(renderId: string, variant?: string): string {
  const base = `/api/timelapse/${renderId}/download`;
  return variant ? `${base}?variant=${encodeURIComponent(variant)}` : base;
}
