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

/** Close an open drawn ring and convert to the API polygon shape. */
export function ringToPolygon(coordinates: [number, number][]): RoiIn {
  return { kind: "polygon", coordinates };
}

export function boundsToBBox(west: number, south: number, east: number, north: number): RoiIn {
  return { kind: "bbox", west, south, east, north };
}
