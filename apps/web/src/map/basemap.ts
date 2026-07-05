// OpenFreeMap vector styles: free, no API key, no usage caps.
// https://openfreemap.org
export const BASEMAP_STYLES = {
  positron: "https://tiles.openfreemap.org/styles/positron",
  liberty: "https://tiles.openfreemap.org/styles/liberty",
  bright: "https://tiles.openfreemap.org/styles/bright",
} as const;

export type BasemapKey = keyof typeof BASEMAP_STYLES;

// Positron's muted greyscale keeps the focus on the data layers on top.
export const DEFAULT_BASEMAP: BasemapKey = "positron";
