/** Pure helpers for the Explore animation transport (unit-tested, no map). */
import type { RoiIn } from "../api/types";

/** Per-frame load status in the browse/preview pool. */
export type FrameStatus = "minting" | "ready" | "error";

/**
 * Bounded prefetch cap: "Prefetch all" only appears at or below this many
 * frames, so the pool never fans out an unbounded number of EE mints.
 */
export const PREFETCH_MAX = 24;

/**
 * Buffer-aware transport step: the next index to display when the play timer
 * ticks. Advances to the next *ready* frame, but **holds** (returns the current
 * index) when the next in-order frame is not ready yet — so playback buffers
 * instead of lying. `error` frames are skipped (they will never load), which is
 * what keeps an all-error pool from deadlocking: the scan is bounded to `total`
 * steps and simply holds when nothing is ready. Wrapping to frame 0 only happens
 * through a ready frame 0.
 */
export function advanceFrame(
  status: Record<number, FrameStatus | undefined>,
  index: number,
  total: number,
): number {
  if (total <= 1) return index;
  for (let step = 1; step <= total; step++) {
    const j = (index + step) % total;
    const s = status[j];
    if (s === "ready") return j; // advance to the next ready frame
    if (s === "error") continue; // permanently failed — skip past it
    return index; // next in-order frame still loading — hold (buffering)
  }
  return index; // nothing ready anywhere (e.g. all error) — hold, never loop
}

/**
 * MapLibre image-source `coordinates`, in the required corner order:
 * top-left, top-right, bottom-right, bottom-left. The wrong order renders the
 * overlay mirrored (not an error), so this is pinned and tested.
 */
export function imageSourceCorners(bbox: {
  west: number;
  south: number;
  east: number;
  north: number;
}): [[number, number], [number, number], [number, number], [number, number]] {
  const { west: w, south: s, east: e, north: n } = bbox;
  return [
    [w, n],
    [e, n],
    [e, s],
    [w, s],
  ];
}

/** Axis-aligned bounds of any ROI (bbox as-is, polygon → its envelope). */
export function roiEnvelope(roi: RoiIn): {
  west: number;
  south: number;
  east: number;
  north: number;
} {
  if (roi.kind === "bbox") {
    return { west: roi.west, south: roi.south, east: roi.east, north: roi.north };
  }
  const lons = roi.coordinates.map(([lon]) => lon);
  const lats = roi.coordinates.map(([, lat]) => lat);
  return {
    west: Math.min(...lons),
    south: Math.min(...lats),
    east: Math.max(...lons),
    north: Math.max(...lats),
  };
}

/** N ISO dates evenly spanning [start, end] inclusive (N ≥ 2). */
export function dateAxis(start: string, end: string, steps: number): string[] {
  const n = Math.max(2, Math.floor(steps));
  const t0 = new Date(start).getTime();
  const t1 = new Date(end).getTime();
  if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) return [start];
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const t = t0 + ((t1 - t0) * i) / (n - 1);
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

/**
 * The pool of frame indices to keep loaded around `index` (a ±`radius` window,
 * clamped to [0, count)). Everything else is evictable.
 */
export function poolIndices(index: number, count: number, radius: number): number[] {
  const out: number[] = [];
  for (let i = Math.max(0, index - radius); i <= Math.min(count - 1, index + radius); i++) {
    out.push(i);
  }
  return out;
}

/** Loaded keys that fall outside the current pool (to be evicted). */
export function evictableKeys(loaded: Iterable<number>, keep: number[]): number[] {
  const keepSet = new Set(keep);
  return [...loaded].filter((k) => !keepSet.has(k));
}
