import { create } from "zustand";

export type StepMode = "interval" | "monthly" | "quarterly";
export type MovieFormat = "mp4" | "gif" | "webm";

/** The Timelapse Studio form. Server data (renders, manifests) stays in
 *  TanStack Query; this store holds only the in-progress form. */
export interface TimelapseForm {
  title: string;
  datasetId: string;
  productKey: string;
  /** ROI source key: "current" | `aoi:${id}` | `preset:${name}`. */
  roiSource: string;
  start: string;
  end: string;
  stepMode: StepMode;
  intervalDays: number;
  windowDays: number | null;
  fps: number;
  format: MovieFormat;
  maxDim: number;
  dateLabel: boolean;
  colorbar: boolean;
  scaleBar: boolean;
  visMin: number | null;
  visMax: number | null;
}

/** A ~1-year window ending today — a sensible default for a monthly timelapse. */
function defaultRange(now: Date = new Date()): { start: string; end: string } {
  const end = now.toISOString().slice(0, 10);
  const startDate = new Date(now);
  startDate.setFullYear(startDate.getFullYear() - 1);
  return { start: startDate.toISOString().slice(0, 10), end };
}

export function defaultForm(): TimelapseForm {
  const { start, end } = defaultRange();
  return {
    title: "",
    datasetId: "s2",
    productKey: "",
    roiSource: "current",
    start,
    end,
    stepMode: "monthly",
    intervalDays: 16,
    windowDays: null,
    fps: 6,
    format: "mp4",
    maxDim: 1080,
    dateLabel: true,
    colorbar: true,
    scaleBar: true,
    visMin: null,
    visMax: null,
  };
}

interface TimelapseState {
  form: TimelapseForm;
  setForm(patch: Partial<TimelapseForm>): void;
  reset(): void;
}

export const useTimelapseStore = create<TimelapseState>()((set) => ({
  form: defaultForm(),
  setForm: (patch) => set((s) => ({ form: { ...s.form, ...patch } })),
  reset: () => set({ form: defaultForm() }),
}));
