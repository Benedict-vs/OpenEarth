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
