/** Pure timelapse helpers: frame-transport math + form → request mapping. */
import type {
  ExtrasConfig,
  GradeConfig,
  Preflight,
  PreflightRequest,
  RoiIn,
  TimelapseRequest,
} from "../api/types";
import type { TimelapseForm } from "../stores/timelapseStore";
import { activePreset, presetModifiesPixels } from "./presets";

// ── Frame transport (drives the rAF player; no DOM here) ──────

/** Milliseconds each frame is shown at a given fps. */
export function frameDurationMs(fps: number): number {
  return 1000 / Math.max(1, fps);
}

/**
 * Advance a play head by one frame. Wraps to 0 when `loop`; otherwise clamps at
 * the last frame so the caller can treat "no change at the end" as stop.
 */
export function advanceIndex(index: number, count: number, loop: boolean): number {
  if (count <= 0) return 0;
  const next = index + 1;
  if (next < count) return next;
  return loop ? 0 : count - 1;
}

/** True once every frame image has finished loading (gates play). */
export function preloadComplete(loaded: number, total: number): boolean {
  return total > 0 && loaded >= total;
}

/**
 * Whole frames elapsed since the last boundary — lets one rAF tick skip ahead
 * after a stall instead of drifting behind real time.
 */
export function framesElapsed(elapsedMs: number, fps: number): number {
  if (elapsedMs <= 0) return 0;
  return Math.floor(elapsedMs / frameDurationMs(fps));
}

// ── Pacing (mirrors core.timelapse.plan_fps — decision 8) ─────

const FPS_MIN = 1;
const FPS_MAX = 30;

function clampFps(fps: number): number {
  return Math.max(FPS_MIN, Math.min(FPS_MAX, Math.round(fps)));
}

/**
 * The single pacing compiler both authoring modes flow through, matching
 * `plan_fps` server-side: duration-first picks the fps that fits `nWindows`
 * frames into the target seconds; frame-first uses the chosen fps. Clamped to
 * [1, 30]. `nWindows` is the total window count (the server plans on windows,
 * not the post-empty frame count).
 */
export function planFps(nWindows: number, form: Pick<TimelapseForm, "authoringMode" | "fps" | "durationS">): number {
  if (form.authoringMode === "duration" && form.durationS > 0) {
    return clampFps(nWindows / form.durationS);
  }
  return clampFps(form.fps);
}

export interface PacingSummary {
  windows: number;
  frames: number;
  fps: number;
  /** Human sentence, e.g. "73 windows → 68 frames @ 12 fps · ~5.7 s". */
  label: string;
}

/** The pacing math the UI shows above Render, resolved from a preflight probe. */
export function pacingSummary(preflight: Preflight, form: TimelapseForm): PacingSummary {
  const windows = preflight.windows.length;
  const frames = preflight.frame_count;
  const fps = planFps(windows, form);
  const seconds = fps > 0 ? frames / fps : 0;
  const arrow = windows === frames ? `${frames} frames` : `${windows} windows → ${frames} frames`;
  return { windows, frames, fps, label: `${arrow} @ ${fps} fps · ~${seconds.toFixed(1)} s` };
}

// ── Form → API request ────────────────────────────────────────

const GIF_MAX_DIM = 720;

/** Cloud-hole handling → the two API fields (`gap_fill`, `cloud_display`). */
export function compileCloud(form: TimelapseForm): { gap_fill: boolean; cloud_display: string } {
  switch (form.cloudMode) {
    case "fill":
      return { gap_fill: true, cloud_display: "composite" };
    case "tint":
      return { gap_fill: false, cloud_display: `tint:${form.tintColor}` };
    case "show":
      return { gap_fill: false, cloud_display: "composite" };
  }
}

/** A grade → `GradeIn`, or `null` when it is the identity (natural + defaults). */
export function compileGrade(form: TimelapseForm): GradeConfig | null {
  const isIdentity =
    form.gradeCurve === "natural" &&
    form.gradeBrightness === 0 &&
    form.gradeContrast === 0 &&
    form.gradeSaturation === 1;
  if (isIdentity) return null;
  return {
    curve: form.gradeCurve,
    brightness: form.gradeBrightness,
    contrast: form.gradeContrast,
    saturation: form.gradeSaturation,
  };
}

function compileExtras(form: TimelapseForm): ExtrasConfig {
  return {
    title_card: form.titleCard.trim() || null,
    end_card: form.endCard.trim() || null,
    watermark: form.watermark.trim() || null,
    crops: form.crops,
  };
}

function compileStep(form: TimelapseForm) {
  return {
    mode: form.stepMode,
    interval_days: form.intervalDays,
    window_days: form.stepMode === "interval" ? form.windowDays : null,
  };
}

/** Map the studio form + a resolved ROI to the API request shape. */
export function buildTimelapseRequest(
  form: TimelapseForm,
  roi: RoiIn,
  opts: { draft?: boolean; productIsRgb?: boolean } = {},
): TimelapseRequest {
  const maxDim = form.format === "gif" ? Math.min(form.maxDim, GIF_MAX_DIM) : form.maxDim;
  // The honesty wall: display-only knobs (fill/tint, deflicker, non-natural grade)
  // are refused on scientific products server-side. Sanitize them at this single
  // choke point so stale form state — e.g. an RGB preset left on after switching to
  // a scientific product — can never reach the API and 422. The UI's disabling of
  // these controls backstops this; it does not replace it.
  const displayOk = opts.productIsRgb ?? true;
  const cloud = displayOk ? compileCloud(form) : { gap_fill: false, cloud_display: "composite" };
  const deflicker = displayOk && form.deflicker;
  const grade = displayOk ? compileGrade(form) : null;
  const preset = activePreset(form);
  const presetId = displayOk || (preset !== null && !presetModifiesPixels(preset)) ? (preset?.id ?? null) : null;

  // Everything except the pacing field: frame-first sends `fps`, duration-first
  // sends `duration_s` and MUST omit `fps` — the API rejects a body carrying both
  // (its model_fields_set check), so the field is added per-mode below.
  const common: Omit<TimelapseRequest, "fps"> = {
    title: form.title.trim() || null,
    dataset: form.datasetId,
    product: form.productKey,
    roi,
    dates: { start: form.start, end: form.end },
    step: compileStep(form),
    format: form.format,
    max_dim: maxDim,
    tween: form.tween,
    annotations: {
      date_label: form.dateLabel,
      colorbar: form.colorbar,
      scale_bar: form.scaleBar,
      attribution: null,
    },
    vis_min: form.visMin,
    vis_max: form.visMax,
    // ── Phase 10 production knobs ──
    preset: presetId,
    composite: form.composite,
    cloud_display: cloud.cloud_display,
    gap_fill: cloud.gap_fill,
    deflicker,
    grade,
    fallback_source: form.fallback,
    draft: opts.draft ?? false,
    extras: compileExtras(form),
  };

  if (form.authoringMode === "duration") {
    return { ...common, duration_s: form.durationS } as TimelapseRequest;
  }
  return { ...common, fps: form.fps };
}

/** The cheap availability probe (decision 11) — collection aggregates only. */
export function buildPreflightRequest(form: TimelapseForm, roi: RoiIn): PreflightRequest {
  return {
    dataset: form.datasetId,
    product: form.productKey,
    roi,
    dates: { start: form.start, end: form.end },
    step: compileStep(form),
    composite: form.composite,
    fallback_source: form.fallback,
  };
}

/**
 * The middle window of a preflight, used to mint a representative preview still.
 * Returns `null` for an empty probe.
 */
export function middleWindow(preflight: Preflight): { start: string; end: string } | null {
  const withData = preflight.windows.filter((w) => w.scene_count > 0);
  const pool = withData.length > 0 ? withData : preflight.windows;
  const w = pool[Math.floor(pool.length / 2)];
  if (!w) return null;
  return { start: w.start, end: w.end };
}
