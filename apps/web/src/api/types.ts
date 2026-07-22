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
export type ThumbnailRequest = components["schemas"]["ThumbnailRequest"];
export type ExportGeotiffRequest = components["schemas"]["ExportGeotiffRequest"];
export type WindField = components["schemas"]["WindFieldOut"];
export type WindSample = components["schemas"]["WindSampleOut"];

// ── Saved AOIs & workspaces ────────────────────────────────
export type Aoi = components["schemas"]["AoiOut"];
export type AoiIn = components["schemas"]["AoiIn"];
export type Workspace = components["schemas"]["WorkspaceOut"];
export type WorkspaceIn = components["schemas"]["WorkspaceIn"];
export type WorkspaceState = components["schemas"]["WorkspaceState"];
export type WorkspaceLayer = components["schemas"]["WorkspaceLayer"];

// ── Jobs & timeseries ──────────────────────────────────────
export type JobCreated = components["schemas"]["JobCreated"];
export type JobOut = components["schemas"]["JobOut"];
export type TimeseriesRequest = components["schemas"]["TimeseriesRequest"];
export type TimeseriesResult = components["schemas"]["TimeseriesResultOut"];
export type TimeseriesPoint = components["schemas"]["TimeseriesPoint"];

// ── Methane Lab ────────────────────────────────────────────
export type Site = components["schemas"]["SiteOut"];
export type SiteIn = components["schemas"]["SiteIn"];
export type SitePatch = components["schemas"]["SitePatch"];
export type SceneInfo = components["schemas"]["SceneInfoOut"];
export type AnalyzeRequest = components["schemas"]["AnalyzeRequest"];
export type Detection = components["schemas"]["DetectionOut"];
export type DetectionDetail = components["schemas"]["DetectionDetailOut"];
export type DetectionPatch = components["schemas"]["DetectionPatch"];
export type ScreeningRequest = components["schemas"]["ScreeningRequest"];
export type MlScanRequest = components["schemas"]["MlScanRequest"];
export type MlStatus = components["schemas"]["MlStatusOut"];
export type NoiseFloor = components["schemas"]["NoiseFloorOut"];
export type ReferenceEvent = components["schemas"]["ReferenceEventOut"];

// ── EMIT plumes (Phase 6) ──────────────────────────────────
export type EmitPlume = components["schemas"]["EmitPlumeOut"];
export type EmitPlumes = components["schemas"]["EmitPlumesOut"];
export type EmitMatch = components["schemas"]["EmitMatchOut"];
export type EmitMatchResult = components["schemas"]["EmitMatchResult"];

// ── Embeddings Explorer (Phase 6) ──────────────────────────
export type EmbeddingTile = components["schemas"]["EmbeddingTileOut"];
export type EmbeddingYears = components["schemas"]["EmbeddingYearsOut"];
export type EmbeddingSimilarityRequest = components["schemas"]["EmbeddingSimilarityRequest"];
export type EmbeddingChangeRequest = components["schemas"]["EmbeddingChangeRequest"];
export type EmbeddingClusterRequest = components["schemas"]["EmbeddingClusterRequest"];

/** Screening hotspots arrive in the job's (untyped) SSE result, not a schema. */
export interface Hotspot {
  lat: number;
  lon: number;
  mean_enh_ppb: number;
  max_enh_ppb: number;
  score: number;
  weeks_flagged: number;
  weeks_observed: number;
}
export type ValidationImport = components["schemas"]["ValidationImportOut"];
export type Validation = components["schemas"]["ValidationOut"];

// ── Timelapse Studio ───────────────────────────────────────
export type TimelapseRequest = components["schemas"]["TimelapseRequest"];
export type TimelapseCreated = components["schemas"]["TimelapseCreated"];
export type Render = components["schemas"]["RenderOut"];
export type RenderDetail = components["schemas"]["RenderDetailOut"];
export type StepConfig = components["schemas"]["StepIn"];
export type AnnotationsConfig = components["schemas"]["AnnotationsIn"];
export type GradeConfig = components["schemas"]["GradeIn"];
export type ExtrasConfig = components["schemas"]["ExtrasIn"];
export type PreflightRequest = components["schemas"]["PreflightRequest"];
export type Preflight = components["schemas"]["PreflightOut"];

/**
 * The parsed `manifest.json` a finished render carries (Phase-10 manifest v2).
 * The generated `RenderDetail.manifest` is an untyped object; this is the shape
 * `FrameManifest.to_dict` writes (packages/core/src/openearth/timelapse.py) — the
 * honesty surfaces (per-frame source / valid / filled) the player + plate read.
 */
export interface ManifestFrame {
  /** Dense movie index of a rendered frame, or null for a skipped window. */
  index: number | null;
  start: string;
  end: string;
  label: string;
  status: "rendered" | "empty" | "failed";
  source: string | null;
  valid_fraction: number | null;
  filled_fraction: number | null;
}
export interface TimelapseManifest {
  dataset: string;
  product: string;
  width: number;
  height: number;
  vis: [number, number];
  cancelled: boolean;
  composite: "mean" | "median" | "clearest";
  post: {
    gap_fill?: boolean;
    gap_fill_cap_windows?: number | null;
    deflicker_strength?: number;
    grade?: { curve: string; brightness: number; contrast: number; saturation: number } | null;
    tint_hole_color?: string | null;
    fallback_source?: string | null;
  };
  frames: ManifestFrame[];
}

/** The Monte-Carlo Q histogram embedded in a detection's `result` blob. */
export interface MethaneHistogram {
  edges: number[];
  counts: number[];
}

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
/** A timelapse render's per-frame preview event (see services/timelapse.py). */
export interface JobFrameData {
  /** Dense movie index of a rendered frame, or null for a skipped window. */
  index: number | null;
  status: "rendered" | "empty" | "failed";
  total: number;
}
export interface JobDoneData {
  status: string;
  result: Record<string, unknown>;
}
export interface JobErrorData {
  status: string;
  detail: string;
}
