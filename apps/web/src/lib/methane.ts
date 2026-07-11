/** Pure helpers for the Methane Lab (no React, no map — unit-tested). */
import type { EChartsCoreOption } from "echarts/core";
import type { BBoxIn, DetectionDetail, MethaneHistogram, Site } from "../api/types";

/**
 * The analysis area sent as the analyze/ML-scan `roi`: a square box centred on
 * (lon, lat). Site ROIs are browse-scale (~100 km) and exceed the 20 m chip
 * limit, so analysis always runs on a chip-sized sub-area of the site.
 */
export interface AnalysisArea {
  lon: number;
  lat: number;
  sizeKm: number;
}

const KM_PER_DEG = 111.32;

/** The server refuses chips over 1024 px; at 20 m that is ~20.5 km per side. */
export const MAX_ANALYSIS_KM = 20;
export const MIN_ANALYSIS_KM = 2;
export const DEFAULT_ANALYSIS_KM = 10;

/** Chip side length in 20 m pixels for a given box size. */
export function analysisAreaPx(sizeKm: number): number {
  return Math.ceil((sizeKm * 1000) / 20);
}

/** The square analysis bbox around the area's centre (lon widened by 1/cos φ). */
export function analysisAreaToBBox(area: AnalysisArea): BBoxIn {
  const halfLat = area.sizeKm / KM_PER_DEG / 2;
  // Clamp cos φ away from 0 so polar centres cannot blow up the width.
  const cosLat = Math.max(Math.cos((area.lat * Math.PI) / 180), 0.01);
  const halfLon = area.sizeKm / (KM_PER_DEG * cosLat) / 2;
  return {
    kind: "bbox",
    west: area.lon - halfLon,
    south: area.lat - halfLat,
    east: area.lon + halfLon,
    north: area.lat + halfLat,
  };
}

/** Default analysis area for a site: centred, 10 km (shrunk to fit small sites). */
export function defaultAnalysisArea(site: Site): AnalysisArea {
  const { west, south, east, north } = site.bbox;
  const lon = (west + east) / 2;
  const lat = (south + north) / 2;
  const cosLat = Math.max(Math.cos((lat * Math.PI) / 180), 0.01);
  const widthKm = (east - west) * KM_PER_DEG * cosLat;
  const heightKm = (north - south) * KM_PER_DEG;
  const fitKm = Math.min(DEFAULT_ANALYSIS_KM, widthKm, heightKm);
  return { lon, lat, sizeKm: Math.max(MIN_ANALYSIS_KM, Math.round(fitKm)) };
}

/** MapLibre image-source corner order [TL, TR, BR, BL]. */
export type ImageCoordinates = [
  [number, number],
  [number, number],
  [number, number],
  [number, number],
];

/**
 * The API's `overlay_bounds` (`[[w,n],[e,n],[e,s],[w,s]]`) is already in
 * MapLibre image-source order; validate the shape and coerce the tuple type.
 */
export function toImageCoordinates(bounds: number[][] | null | undefined): ImageCoordinates | null {
  if (!bounds || bounds.length !== 4) return null;
  return bounds.map(([lon, lat]) => [lon, lat]) as ImageCoordinates;
}

/** kg/h → tonnes/hour, the display unit for emission rates. */
export function kghToTh(value: number | null | undefined): number | null {
  return value == null ? null : value / 1000;
}

/** Format an emission rate ± σ as "Q ± σ t/h" (or "—" when absent). */
export function formatEmission(qKgh: number | null, sigmaKgh: number | null): string {
  const q = kghToTh(qKgh);
  if (q == null) return "—";
  const s = kghToTh(sigmaKgh);
  const body = s == null ? q.toFixed(1) : `${q.toFixed(1)} ± ${s.toFixed(1)}`;
  return `${body} t/h`;
}

export interface VerdictBadge {
  label: string;
  className: string;
}

/** Map a validation verdict to a display label + CSS modifier class. */
export function verdictBadge(verdict: string | null | undefined): VerdictBadge {
  switch (verdict) {
    case "confirmed":
      return { label: "Confirmed", className: "verdict confirmed" };
    case "plausible":
      return { label: "Plausible", className: "verdict plausible" };
    case "contradicted":
      return { label: "Contradicted", className: "verdict contradicted" };
    case "unvalidated":
      return { label: "Unvalidated", className: "verdict unvalidated" };
    default:
      return { label: "Not validated", className: "verdict none" };
  }
}

/** Build an ECharts bar option for the Monte-Carlo Q histogram (edges in kg/h). */
export function histogramOption(histogram: MethaneHistogram | undefined): EChartsCoreOption {
  const edges = histogram?.edges ?? [];
  const counts = histogram?.counts ?? [];
  // Bin centre (t/h) for each of the N-1 bars.
  const centers = counts.map((_c, i) =>
    (((edges[i] ?? 0) + (edges[i + 1] ?? 0)) / 2 / 1000).toFixed(1),
  );
  return {
    grid: { left: 44, right: 12, top: 12, bottom: 28 },
    xAxis: {
      type: "category",
      data: centers,
      name: "Q (t/h)",
      nameLocation: "middle",
      nameGap: 22,
      axisLabel: { fontSize: 10 },
    },
    yAxis: { type: "value", name: "draws", axisLabel: { fontSize: 10 } },
    tooltip: { trigger: "axis" },
    series: [{ type: "bar", data: counts, itemStyle: { color: "#d9534f" }, barCategoryGap: "10%" }],
  };
}

/** The rows of the detection "numbers" table, formatted for display. */
export interface NumberRow {
  label: string;
  value: string;
}

function fmt(value: unknown, digits = 2, unit = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

export function detectionNumbers(detail: DetectionDetail): NumberRow[] {
  const r = (detail.result ?? {}) as Record<string, unknown>;
  const calib = (r.calibration ?? {}) as Record<string, unknown>;
  return [
    { label: "Q (median)", value: formatEmission(detail.q_kg_h, detail.q_sigma_kg_h) },
    { label: "IME", value: fmt(detail.ime_kg, 1, "kg") },
    { label: "Plume length L", value: fmt(r.l_m, 0, "m") },
    { label: "U_eff", value: fmt(r.u_eff_ms, 2, "m/s") },
    { label: "U10 ± σ", value: `${fmt(r.u10_ms, 1)} ± ${fmt(r.sigma_u10_ms, 1)} m/s` },
    { label: "Wind from", value: fmt(detail.wind_from_deg, 0, "°") },
    { label: "ΔXCH4 max", value: fmt(detail.xch4_max_ppb, 0, "ppb") },
    { label: "c (target)", value: fmt(calib.c_target, 4) },
    { label: "c (reference)", value: fmt(calib.c_ref, 4) },
  ];
}

/** Numbers for an ML candidate: single-pass Q (no Monte-Carlo σ) + footprint stats. */
export function mlDetectionNumbers(detail: DetectionDetail): NumberRow[] {
  const r = (detail.result ?? {}) as Record<string, unknown>;
  return [
    { label: "Q (single-pass)", value: formatEmission(detail.q_kg_h, detail.q_sigma_kg_h) },
    { label: "IME", value: fmt(detail.ime_kg, 1, "kg") },
    { label: "U10", value: fmt(detail.u10_ms, 1, "m/s") },
    { label: "Wind from", value: fmt(detail.wind_from_deg, 0, "°") },
    { label: "ΔXCH4 max", value: fmt(detail.xch4_max_ppb, 0, "ppb") },
    { label: "Candidates", value: fmt(r.n_candidates, 0) },
  ];
}

/** Map a physics/ML disagreement flag to a display label + CSS modifier class. */
export function disagreementBadge(flag: string | null | undefined): VerdictBadge | null {
  switch (flag) {
    case "agree":
      return { label: "Physics agrees", className: "disagreement agree" };
    case "ml_only":
      return { label: "ML-only (no physics detection)", className: "disagreement ml-only" };
    default:
      return null;
  }
}
