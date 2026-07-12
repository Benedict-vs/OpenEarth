/**
 * The window/period time model (pure, unit-tested — no store, no map).
 *
 * Two named concepts used with the same vocabulary everywhere:
 *  - **Window**: the time slice a single composite averages over (center ± halfDays).
 *  - **Period**: a span of time you look *across* (chart, preview, timelapse, search).
 *
 * A window compiles to a `composite: "mean"` request over {@link windowMeanDates},
 * NOT `date_window` — the exclusive +1-day end reproduces the server's
 * `build_date_composite` semantics ([center−h, center+h+1)) while keeping the
 * width off the tiles schema's `half_window_days` cap (le=30). `windowRange` is
 * the inclusive display range (caption + migration inverse).
 */

export interface TimeWindow {
  /** ISO date (YYYY-MM-DD) the composite is centered on. */
  center: string;
  /** Half-width in days; the window covers center ± halfDays inclusive. */
  halfDays: number;
}

export interface Period {
  start: string;
  end: string;
}

/** Custom ± bound for a window width (integer days). */
export const WINDOW_HALF_MIN = 0;
export const WINDOW_HALF_MAX = 183;

export interface WindowPreset {
  label: string;
  halfDays: number;
}

/** Pinned width presets (see the vocabulary contract). */
export const WINDOW_PRESETS: readonly WindowPreset[] = [
  { label: "Day", halfDays: 0 },
  { label: "±3 d", halfDays: 3 },
  { label: "±15 d", halfDays: 15 },
  { label: "±45 d", halfDays: 45 },
];

const MS_PER_DAY = 24 * 3600 * 1000;

function isoDayMs(iso: string): number {
  return new Date(`${iso}T00:00:00Z`).getTime();
}

function msToIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  return msToIso(isoDayMs(iso) + days * MS_PER_DAY);
}

function todayIso(now: Date): string {
  return now.toISOString().slice(0, 10);
}

export function clampHalfDays(days: number): number {
  const d = Math.round(days);
  if (!Number.isFinite(d)) return WINDOW_HALF_MIN;
  return Math.min(WINDOW_HALF_MAX, Math.max(WINDOW_HALF_MIN, d));
}

/**
 * Inclusive display range: [center − halfDays, min(center + halfDays, today)].
 * A future-leaning window is legal input; the end is clamped to today (the
 * caption shows the clamped range).
 */
export function windowRange(w: TimeWindow, now: Date = new Date()): Period {
  const start = addDays(w.center, -w.halfDays);
  const rawEnd = addDays(w.center, w.halfDays);
  const today = todayIso(now);
  return { start, end: rawEnd > today ? today : rawEnd };
}

/**
 * The `dates` for the `composite: "mean"` request that renders this window:
 * same start as {@link windowRange}, but an EXCLUSIVE end (display end + 1 day).
 * This matches the server's `date_window` semantics without touching the tiles
 * schema. The ±0 (Day) case is [center, center+1) — exactly one day.
 */
export function windowMeanDates(w: TimeWindow, now: Date = new Date()): Period {
  const { start, end } = windowRange(w, now);
  return { start, end: addDays(end, 1) };
}

/**
 * Midpoint + ceil(span/2) window for an inclusive [start, end] range. The
 * v1-workspace migration primitive; the inverse of {@link windowRange} for an
 * unclamped range.
 */
export function rangeToWindow(start: string, end: string): TimeWindow {
  const spanDays = Math.max(0, Math.round((isoDayMs(end) - isoDayMs(start)) / MS_PER_DAY));
  return { center: addDays(start, Math.round(spanDays / 2)), halfDays: Math.ceil(spanDays / 2) };
}

/** The pinned window caption, e.g. `≙ 2026-05-28 → 2026-06-27 · mean composite, clouds masked`. */
export function formatWindowCaption(w: TimeWindow, now: Date = new Date()): string {
  const { start, end } = windowRange(w, now);
  return `≙ ${start} → ${end} · mean composite, clouds masked`;
}

/** New-session default window: today − 15 d, ±15 d (≈ the old last-30-days composite). */
export function defaultWindow(now: Date = new Date()): TimeWindow {
  return { center: addDays(todayIso(now), -15), halfDays: 15 };
}

/** New-session default period: the last 12 months (end = today). */
export function defaultPeriod(now: Date = new Date()): Period {
  const end = todayIso(now);
  return { start: addDays(end, -365), end };
}
