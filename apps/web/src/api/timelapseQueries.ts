/** React-query hooks + plain calls for the Timelapse Studio API. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPost } from "./client";
import type { Render, RenderDetail, TimelapseCreated, TimelapseRequest } from "./types";

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

/** URL of the encoded movie download. */
export function downloadUrl(renderId: string): string {
  return `/api/timelapse/${renderId}/download`;
}
