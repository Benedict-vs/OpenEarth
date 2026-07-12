import { create } from "zustand";
import type { Site } from "../api/types";
import { type AnalysisArea, defaultAnalysisArea } from "../lib/methane";

export interface RunParams {
  method: "mbmp" | "mbsp";
  referenceMode: "single" | "composite";
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
  /** The target scene's acquisition time — needed to mint its RGB preview tiles. */
  targetSceneTime: string | null;
  /** "auto" selects a reference server-side (MBMP); or an explicit scene id. */
  referenceSceneId: string | "auto";
  params: RunParams;
  selectedDetectionId: string | null;
  job: JobProgress | null;
  /** EMIT plume overlay on the Lab map (independent evidence, not our detections). */
  emitPlumesEnabled: boolean;
  /** The chip-sized sub-area analyzed within the site (site ROIs are browse-scale). */
  analysisArea: AnalysisArea | null;
  /** True while the next Lab-map click recentres the analysis area. */
  placingArea: boolean;
  /** True-colour preview of the target scene on the Lab map (helps area selection). */
  rgbPreviewEnabled: boolean;
  /** ΔXCH4 detection overlay visibility + opacity (bare-eye RGB comparison). */
  overlayVisible: boolean;
  overlayOpacity: number;

  selectSite(site: Site | null): void;
  setDates(start: string, end: string): void;
  setTarget(sceneId: string | null, time?: string | null): void;
  setReference(sceneId: string | "auto"): void;
  setParams(params: Partial<RunParams>): void;
  selectDetection(id: string | null): void;
  setJob(job: JobProgress | null): void;
  setEmitPlumes(enabled: boolean): void;
  setAnalysisArea(area: Partial<AnalysisArea>): void;
  setPlacingArea(placing: boolean): void;
  setRgbPreview(enabled: boolean): void;
  setOverlayVisible(visible: boolean): void;
  setOverlayOpacity(opacity: number): void;
}

const DEFAULT_PARAMS: RunParams = {
  method: "mbmp",
  referenceMode: "single",
  kSigma: 2,
  minAreaPx: 5,
  seed: 0,
};

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
  targetSceneTime: null,
  referenceSceneId: "auto",
  params: DEFAULT_PARAMS,
  selectedDetectionId: null,
  job: null,
  emitPlumesEnabled: false,
  analysisArea: null,
  placingArea: false,
  rgbPreviewEnabled: true,
  overlayVisible: true,
  overlayOpacity: 0.85,

  selectSite: (site) =>
    set({
      selectedSite: site,
      dates: datesFromSite(site),
      targetSceneId: null,
      targetSceneTime: null,
      referenceSceneId: "auto",
      selectedDetectionId: null,
      analysisArea: site ? defaultAnalysisArea(site) : null,
      placingArea: false,
    }),
  setDates: (start, end) => set({ dates: { start, end } }),
  setTarget: (sceneId, time = null) =>
    set({ targetSceneId: sceneId, targetSceneTime: sceneId ? time : null }),
  setReference: (sceneId) => set({ referenceSceneId: sceneId }),
  setParams: (params) => set((s) => ({ params: { ...s.params, ...params } })),
  selectDetection: (id) => set({ selectedDetectionId: id }),
  setJob: (job) => set({ job }),
  setEmitPlumes: (enabled) => set({ emitPlumesEnabled: enabled }),
  setAnalysisArea: (area) =>
    set((s) => (s.analysisArea ? { analysisArea: { ...s.analysisArea, ...area } } : s)),
  setPlacingArea: (placing) => set({ placingArea: placing }),
  setRgbPreview: (enabled) => set({ rgbPreviewEnabled: enabled }),
  setOverlayVisible: (visible) => set({ overlayVisible: visible }),
  setOverlayOpacity: (opacity) => set({ overlayOpacity: opacity }),
}));
