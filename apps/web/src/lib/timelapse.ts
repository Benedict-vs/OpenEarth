/** Pure timelapse helpers: frame-transport math + form → request mapping. */
import type { RoiIn, TimelapseRequest } from "../api/types";
import type { TimelapseForm } from "../stores/timelapseStore";

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

// ── Form → API request ────────────────────────────────────────

const GIF_MAX_DIM = 720;

/** Map the studio form + a resolved ROI to the API request shape. */
export function buildTimelapseRequest(form: TimelapseForm, roi: RoiIn): TimelapseRequest {
  const maxDim = form.format === "gif" ? Math.min(form.maxDim, GIF_MAX_DIM) : form.maxDim;
  return {
    title: form.title.trim() || null,
    dataset: form.datasetId,
    product: form.productKey,
    roi,
    dates: { start: form.start, end: form.end },
    step: {
      mode: form.stepMode,
      interval_days: form.intervalDays,
      window_days: form.stepMode === "interval" ? form.windowDays : null,
    },
    fps: form.fps,
    format: form.format,
    max_dim: maxDim,
    annotations: {
      date_label: form.dateLabel,
      colorbar: form.colorbar,
      scale_bar: form.scaleBar,
      attribution: null,
    },
    vis_min: form.visMin,
    vis_max: form.visMax,
  };
}
