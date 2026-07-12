import { create } from "zustand";
import type { VizOverrides } from "../api/types";
import { defaultDateRange } from "../lib/time";

export type CompareMode = "linked" | "independent";
export type CompareOrientation = "vertical" | "horizontal";

export interface SideConfig {
  dataset: string;
  product: string;
  viz: VizOverrides | null;
  /** Window center for the side's composite. */
  date: string;
  /** Window half-width in days (the smoothing control; default 3 ≈ "1 week"). */
  halfDays: number;
}

/** The shared fields (dataset/product/viz) that linked-mode fans out to both. */
type SharedConfig = Pick<SideConfig, "dataset" | "product" | "viz">;

interface CompareState {
  mode: CompareMode;
  orientation: CompareOrientation;
  left: SideConfig;
  right: SideConfig;
  setMode(mode: CompareMode): void;
  setOrientation(orientation: CompareOrientation): void;
  /** Patch one side (independent mode: any field; both modes: the date). */
  setSide(side: "left" | "right", patch: Partial<SideConfig>): void;
  /** Linked mode: change the shared dataset/product/viz on *both* sides. */
  setShared(patch: Partial<SharedConfig>): void;
}

function initialSides(): { left: SideConfig; right: SideConfig } {
  const { start, end } = defaultDateRange();
  const shared: SharedConfig = { dataset: "s2", product: "NDVI", viz: null };
  // Linked default: same layer, two dates (the classic change comparison).
  // halfDays 3 keeps the pre-Phase-8 ±3 d behaviour as the migration default.
  return {
    left: { ...shared, date: start, halfDays: 3 },
    right: { ...shared, date: end, halfDays: 3 },
  };
}

export const useCompareStore = create<CompareState>()((set) => ({
  mode: "linked",
  orientation: "vertical",
  ...initialSides(),

  setMode: (mode) => set({ mode }),
  setOrientation: (orientation) => set({ orientation }),

  setSide: (side, patch) =>
    set((s) => ({ [side]: { ...s[side], ...patch } }) as Pick<CompareState, "left" | "right">),

  setShared: (patch) =>
    set((s) => ({
      left: { ...s.left, ...patch },
      right: { ...s.right, ...patch },
    })),
}));
