import { create } from "zustand";
import type { RoiIn } from "../api/types";

interface RoiState {
  roi: RoiIn | null;
  /** Name of the applied preset, if the ROI came from one. */
  presetName: string | null;
  setRoi(roi: RoiIn | null): void;
  applyPreset(name: string, roi: RoiIn): void;
  clear(): void;
}

export const useRoiStore = create<RoiState>()((set) => ({
  roi: null,
  presetName: null,
  setRoi: (roi) => set({ roi, presetName: null }),
  applyPreset: (name, roi) => set({ roi, presetName: name }),
  clear: () => set({ roi: null, presetName: null }),
}));

/** Convert a drawn ring to the API polygon shape (closed ring accepted). */
export function ringToPolygon(coordinates: [number, number][]): RoiIn {
  return { kind: "polygon", coordinates };
}

export function boundsToBBox(west: number, south: number, east: number, north: number): RoiIn {
  return { kind: "bbox", west, south, east, north };
}

/** Axis-aligned bounds of a drawn rectangle ring → bbox ROI. */
export function ringToBBox(ring: [number, number][]): RoiIn {
  const lons = ring.map(([lon]) => lon);
  const lats = ring.map(([, lat]) => lat);
  return boundsToBBox(Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats));
}

/** Bounds of any ROI, e.g. for map.fitBounds. */
export function roiBounds(roi: RoiIn): [number, number, number, number] {
  if (roi.kind === "bbox") return [roi.west, roi.south, roi.east, roi.north];
  return roiBoundsOfRing(roi.coordinates as [number, number][]);
}

function roiBoundsOfRing(ring: [number, number][]): [number, number, number, number] {
  const lons = ring.map(([lon]) => lon);
  const lats = ring.map(([, lat]) => lat);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}
