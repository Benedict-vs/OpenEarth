/** Friendly aliases over the generated OpenAPI types (src/api/types.gen.ts). */
import type { components } from "./types.gen";

export type Dataset = components["schemas"]["DatasetOut"];
export type Product = components["schemas"]["ProductOut"];
export type Legend = components["schemas"]["LegendOut"];
export type TilesRequest = components["schemas"]["TilesRequest"];
export type TileResponse = components["schemas"]["TileResponse"];
export type BBoxIn = components["schemas"]["BBoxIn"];
export type PolygonIn = components["schemas"]["PolygonIn"];
export type RoiIn = BBoxIn | PolygonIn;
export type DateRange = components["schemas"]["DateRangeIn"];
export type VizOverrides = components["schemas"]["VizOverrides"];
export type RoiPreset = components["schemas"]["RoiPresetOut"];
export type AppConfig = components["schemas"]["ConfigOut"];
export type Scene = components["schemas"]["SceneOut"];
export type InspectRequest = components["schemas"]["InspectRequest"];
export type InspectResult = components["schemas"]["InspectResult"];

// ── Jobs & timeseries ──────────────────────────────────────
export type JobCreated = components["schemas"]["JobCreated"];
export type JobOut = components["schemas"]["JobOut"];
export type TimeseriesRequest = components["schemas"]["TimeseriesRequest"];
export type TimeseriesResult = components["schemas"]["TimeseriesResultOut"];
export type TimeseriesPoint = components["schemas"]["TimeseriesPoint"];

// SSE payloads are streamed, not part of the OpenAPI schema, so they are
// hand-typed here to match the wire format pinned in
// docs/phase2-execution-plan.md ("SSE wire format"). Keep them in sync with
// packages/api/src/openearth_api/jobs.py.
export interface JobProgressData {
  done: number;
  total: number;
  message: string | null;
}
export interface JobPointsData {
  points: TimeseriesPoint[];
}
export interface JobDoneData {
  status: string;
  result: Record<string, unknown>;
}
export interface JobErrorData {
  status: string;
  detail: string;
}
