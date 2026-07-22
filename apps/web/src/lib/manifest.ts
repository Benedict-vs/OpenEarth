/**
 * Pure readers over a finished render's manifest v2 (see `TimelapseManifest`).
 * The manifest is the single source of the per-frame honesty surfaces (hard
 * rule 3): source, measured (valid) fraction, and borrowed (filled) fraction.
 * The player badges, availability recap, and the citable plate all read them
 * through here so the numbers are computed once, the same way.
 */
import type { ManifestFrame, RenderDetail, TimelapseManifest } from "../api/types";

export function parseManifest(detail: RenderDetail | null | undefined): TimelapseManifest | null {
  if (!detail?.manifest) return null;
  return detail.manifest as unknown as TimelapseManifest;
}

/** Rendered frames only (a real movie index), in playback order. */
export function renderedFrames(m: TimelapseManifest): ManifestFrame[] {
  return m.frames
    .filter((f): f is ManifestFrame & { index: number } => f.index !== null)
    .sort((a, b) => a.index - b.index);
}

export interface FrameQc {
  source: string | null;
  valid: number | null;
  filled: number | null;
  label: string;
}

/** QC for the frame at a given dense movie index (what the player is showing). */
export function frameQc(m: TimelapseManifest | null, denseIndex: number): FrameQc | null {
  if (!m) return null;
  const f = m.frames.find((fr) => fr.index === denseIndex);
  if (!f) return null;
  return { source: f.source, valid: f.valid_fraction, filled: f.filled_fraction, label: f.label };
}

export type SourceKind = "primary" | "fallback" | "gap";

/** Classify a frame's source relative to the render's primary dataset. */
export function sourceKind(source: string | null, primary: string): SourceKind {
  if (!source) return "gap";
  return source === primary ? "primary" : "fallback";
}

export interface CoverageSummary {
  windows: number;
  rendered: number;
  empty: number;
  /** Frames that borrowed at least one pixel via gap-fill. */
  borrowed: number;
  /** source dataset → rendered-frame count (e.g. {s2: 9, hls: 2}). */
  sources: Record<string, number>;
  /** Mean measured (valid) fraction across rendered frames, 0–1, or null. */
  meanValid: number | null;
  /** Mean borrowed (filled) fraction across rendered frames, 0–1, or null. */
  meanFilled: number | null;
}

export function coverageSummary(m: TimelapseManifest): CoverageSummary {
  const rendered = renderedFrames(m);
  const sources: Record<string, number> = {};
  let validSum = 0;
  let validN = 0;
  let filledSum = 0;
  let filledN = 0;
  let borrowed = 0;
  for (const f of rendered) {
    if (f.source) sources[f.source] = (sources[f.source] ?? 0) + 1;
    if (f.valid_fraction != null) {
      validSum += f.valid_fraction;
      validN += 1;
    }
    if (f.filled_fraction != null) {
      filledSum += f.filled_fraction;
      filledN += 1;
      if (f.filled_fraction > 0) borrowed += 1;
    }
  }
  return {
    windows: m.frames.length,
    rendered: rendered.length,
    empty: m.frames.filter((f) => f.status !== "rendered").length,
    borrowed,
    sources,
    meanValid: validN > 0 ? validSum / validN : null,
    meanFilled: filledN > 0 ? filledSum / filledN : null,
  };
}

/** A 0–1 fraction as a compact percentage, e.g. 0.962 → "96%". */
export function pct(fraction: number | null | undefined, digits = 0): string {
  if (fraction == null) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}
