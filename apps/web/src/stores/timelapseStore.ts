import { create } from "zustand";

export type StepMode = "interval" | "monthly" | "quarterly";
export type MovieFormat = "mp4" | "gif" | "webm";
export type CompositeMode = "mean" | "median" | "clearest";
/** How cloud holes are shown: filled, flagged with a tint, or left as the composite. */
export type CloudMode = "fill" | "tint" | "show";
export type GradeCurve = "natural" | "vivid" | "cinematic";
/** Frame-first (pick an fps) or duration-first (pick a length, fps derived). */
export type AuthoringMode = "fps" | "duration";
export type CropRatio = "1:1" | "9:16";

/** The Timelapse Studio form. Server data (renders, manifests) stays in
 *  TanStack Query; this store holds only the in-progress form.
 *
 *  Every Phase-10 knob defaults to the legacy behaviour (mean composite, no
 *  post-processing, no fallback, frame-first fps) so a fresh form compiles to a
 *  byte-equivalent legacy request (`lib/timelapse.ts`). */
export interface TimelapseForm {
  // ── Source (Area) ──
  title: string;
  datasetId: string;
  productKey: string;
  /** ROI source key: "current" | `aoi:${id}` | `preset:${name}`. */
  roiSource: string;
  // ── Span ──
  start: string;
  end: string;
  stepMode: StepMode;
  intervalDays: number;
  windowDays: number | null;
  // ── Look / recipe (a preset sets exactly these) ──
  composite: CompositeMode;
  cloudMode: CloudMode;
  /** Hole-flag colour for `cloudMode === "tint"` (Survey), as `#rrggbb`. */
  tintColor: string;
  deflicker: boolean;
  fallback: boolean;
  // ── Grade (flat so sliders + preset-matching stay simple) ──
  gradeCurve: GradeCurve;
  gradeBrightness: number;
  gradeContrast: number;
  gradeSaturation: number;
  // ── Motion / pacing ──
  authoringMode: AuthoringMode;
  fps: number;
  durationS: number;
  /** Frame-to-frame smoothing: cross-fades inserted between frames (0 = off). */
  tween: number;
  format: MovieFormat;
  // ── Resolution ──
  maxDim: number;
  // ── Vis + annotations ──
  visMin: number | null;
  visMax: number | null;
  dateLabel: boolean;
  colorbar: boolean;
  scaleBar: boolean;
  // ── Share extras ──
  titleCard: string;
  endCard: string;
  watermark: string;
  crops: CropRatio[];
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
    composite: "mean",
    cloudMode: "show",
    tintColor: "#e34a6f",
    deflicker: false,
    fallback: false,
    gradeCurve: "natural",
    gradeBrightness: 0,
    gradeContrast: 0,
    gradeSaturation: 1,
    authoringMode: "fps",
    fps: 6,
    durationS: 15,
    tween: 0,
    format: "mp4",
    maxDim: 1080,
    visMin: null,
    visMax: null,
    dateLabel: true,
    colorbar: true,
    scaleBar: true,
    titleCard: "",
    endCard: "",
    watermark: "",
    crops: [],
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
