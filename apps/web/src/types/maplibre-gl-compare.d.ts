/**
 * Ambient types for @maplibre/maplibre-gl-compare (0.5.0 ships no TS types).
 * Constructor is `new Compare(beforeMap, afterMap, container, options?)`.
 */
declare module "@maplibre/maplibre-gl-compare" {
  import type { Map as MapLibreMap } from "maplibre-gl";

  export interface CompareOptions {
    orientation?: "vertical" | "horizontal";
    mousemove?: boolean;
  }

  export default class Compare {
    constructor(
      before: MapLibreMap,
      after: MapLibreMap,
      container: HTMLElement | string,
      options?: CompareOptions,
    );
    setSlider(x: number): void;
    remove(): void;
  }
}

declare module "@maplibre/maplibre-gl-compare/dist/maplibre-gl-compare.css";
