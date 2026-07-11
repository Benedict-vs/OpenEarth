import { create } from "zustand";
import type { Site } from "../api/types";

export interface RunParams {
  method: "mbmp" | "mbsp";
  kSigma: number;
  minAreaPx: number;
  seed: number;
}

export interface JobProgress {
  jobId: string;
  step: number;
  total: number;
  message: string | null;
  status: "running" | "done" | "error";
  detail?: string;
}

interface MethaneState {
  selectedSite: Site | null;
  dates: { start: string; end: string };
  targetSceneId: string | null;
  /** "auto" selects a reference server-side (MBMP); or an explicit scene id. */
  referenceSceneId: string | "auto";
  params: RunParams;
  selectedDetectionId: string | null;
  job: JobProgress | null;
  /** EMIT plume overlay on the Lab map (independent evidence, not our detections). */
  emitPlumesEnabled: boolean;

  selectSite(site: Site | null): void;
  setDates(start: string, end: string): void;
  setTarget(sceneId: string | null): void;
  setReference(sceneId: string | "auto"): void;
  setParams(params: Partial<RunParams>): void;
  selectDetection(id: string | null): void;
  setJob(job: JobProgress | null): void;
  setEmitPlumes(enabled: boolean): void;
}

const DEFAULT_PARAMS: RunParams = { method: "mbmp", kSigma: 2, minAreaPx: 5, seed: 0 };

/** Seed the date range from a site's date hint (or a neutral fallback). */
function datesFromSite(site: Site | null): { start: string; end: string } {
  if (site?.date_hint_start && site?.date_hint_end) {
    return { start: site.date_hint_start, end: site.date_hint_end };
  }
  return { start: "2024-06-01", end: "2024-09-01" };
}

export const useMethaneStore = create<MethaneState>()((set) => ({
  selectedSite: null,
  dates: datesFromSite(null),
  targetSceneId: null,
  referenceSceneId: "auto",
  params: DEFAULT_PARAMS,
  selectedDetectionId: null,
  job: null,
  emitPlumesEnabled: false,

  selectSite: (site) =>
    set({
      selectedSite: site,
      dates: datesFromSite(site),
      targetSceneId: null,
      referenceSceneId: "auto",
      selectedDetectionId: null,
    }),
  setDates: (start, end) => set({ dates: { start, end } }),
  setTarget: (sceneId) => set({ targetSceneId: sceneId }),
  setReference: (sceneId) => set({ referenceSceneId: sceneId }),
  setParams: (params) => set((s) => ({ params: { ...s.params, ...params } })),
  selectDetection: (id) => set({ selectedDetectionId: id }),
  setJob: (job) => set({ job }),
  setEmitPlumes: (enabled) => set({ emitPlumesEnabled: enabled }),
}));
