/**
 * Analysis panel state + the "Run series" orchestration.
 *
 * A run fires two jobs concurrently: a coarse pass (4× pixel size → fast
 * preview) and the native pass. Both stream `points` events that merge into
 * their respective maps; the render rule (mergeCoarseFine) shows fine where it
 * exists, else coarse, so native chunks visibly replace the preview as they
 * land. Per the SSE contract, `points` are a preview only — on each job's
 * `done` we refetch the authoritative result via the result endpoint.
 */
import { create } from "zustand";
import { apiGet, apiPost } from "../api/client";
import { subscribeJob } from "../api/sse";
import type {
  JobCreated,
  RoiIn,
  TimeseriesPoint,
  TimeseriesRequest,
  TimeseriesResult,
} from "../api/types";
import type { SeriesPoint } from "../lib/series";

export interface AnalysisMeta {
  unit: string;
  displayScale: number;
  band: string;
  scaleM: number;
}

export interface RunParams {
  dataset: string;
  product: string;
  label: string;
  roi: RoiIn;
  start: string;
  end: string;
}

export type AnalysisStatus = "idle" | "running" | "done" | "error";

interface AnalysisState {
  open: boolean;
  /** Explicit layer choice; `null` → the panel uses its default (topmost). */
  layerId: string | null;
  status: AnalysisStatus;
  coarse: Map<string, SeriesPoint>;
  fine: Map<string, SeriesPoint>;
  /** Native job id — drives the CSV download link once it succeeds. */
  fineJobId: string | null;
  progress: { done: number; total: number } | null;
  meta: AnalysisMeta | null;
  label: string | null;
  range: { start: string; end: string } | null;
  error: string | null;
  _cleanup: (() => void) | null;

  setOpen: (open: boolean) => void;
  toggleOpen: () => void;
  selectLayer: (id: string | null) => void;
  run: (params: RunParams) => Promise<void>;
  reset: () => void;
}

function pointsToMap(points: TimeseriesPoint[]): Map<string, SeriesPoint> {
  return new Map(points.map((p) => [p.date, { date: p.date, value: p.value, count: p.count }]));
}

const IDLE = {
  status: "idle" as AnalysisStatus,
  coarse: new Map<string, SeriesPoint>(),
  fine: new Map<string, SeriesPoint>(),
  fineJobId: null,
  progress: null,
  meta: null,
  label: null,
  range: null,
  error: null,
  _cleanup: null,
};

export const useAnalysisStore = create<AnalysisState>()((set, get) => ({
  open: false,
  layerId: null,
  ...IDLE,

  setOpen: (open) => set({ open }),
  toggleOpen: () => set((state) => ({ open: !state.open })),
  selectLayer: (layerId) => set({ layerId }),

  reset: () => {
    get()._cleanup?.();
    set(IDLE);
  },

  run: async (params) => {
    get()._cleanup?.();
    set({
      ...IDLE,
      status: "running",
      coarse: new Map(),
      fine: new Map(),
      open: true,
      label: params.label,
      range: { start: params.start, end: params.end },
    });

    const request = (scale: "coarse" | "native"): TimeseriesRequest => ({
      dataset: params.dataset,
      product: params.product,
      roi: params.roi,
      dates: { start: params.start, end: params.end },
      scale,
    });

    const mergePoints = (which: "coarse" | "fine", points: TimeseriesPoint[]) =>
      set((state) => {
        const next = new Map(which === "coarse" ? state.coarse : state.fine);
        for (const p of points) next.set(p.date, { date: p.date, value: p.value, count: p.count });
        return which === "coarse" ? { coarse: next } : { fine: next };
      });

    const loadResult = (jobId: string) =>
      apiGet<TimeseriesResult>(`/api/timeseries/${jobId}/result?format=json`);

    try {
      const [coarseJob, fineJob] = await Promise.all([
        apiPost<JobCreated>("/api/timeseries", request("coarse")),
        apiPost<JobCreated>("/api/timeseries", request("native")),
      ]);

      const unsubCoarse = subscribeJob(coarseJob.job_id, {
        onPoints: (d) => mergePoints("coarse", d.points),
        onDone: () => {
          // Authoritative coarse series; ignore failures — fine is truth.
          loadResult(coarseJob.job_id)
            .then((result) => set({ coarse: pointsToMap(result.points) }))
            .catch(() => {});
        },
        onError: (d) => set({ status: "error", error: d.detail }),
      });

      const unsubFine = subscribeJob(fineJob.job_id, {
        onProgress: (d) => set({ progress: { done: d.done, total: d.total } }),
        onPoints: (d) => mergePoints("fine", d.points),
        onDone: () => {
          loadResult(fineJob.job_id)
            .then((result) =>
              set({
                fine: pointsToMap(result.points),
                meta: {
                  unit: result.unit,
                  displayScale: result.display_scale,
                  band: result.band,
                  scaleM: result.scale_m,
                },
                status: "done",
                progress: null,
              }),
            )
            .catch((e: unknown) =>
              set({ status: "error", error: e instanceof Error ? e.message : String(e) }),
            );
        },
        onError: (d) => set({ status: "error", error: d.detail }),
      });

      set({
        fineJobId: fineJob.job_id,
        _cleanup: () => {
          unsubCoarse();
          unsubFine();
        },
      });
    } catch (e) {
      set({ status: "error", error: e instanceof Error ? e.message : String(e) });
    }
  },
}));
