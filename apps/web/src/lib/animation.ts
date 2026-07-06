/** Pure helpers for the Explore animation transport (unit-tested, no map). */
import type { RoiIn } from "../api/types";

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
