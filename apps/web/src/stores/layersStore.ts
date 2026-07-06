import { create } from "zustand";
import type { Legend, VizOverrides } from "../api/types";

export interface LayerMint {
  tileUrl: string;
  /** Epoch ms. */
  mintedAt: number;
  expiresAt: number;
  attribution: string;
  legend: Legend;
  /** Serialized mint parameters — lets remounts skip redundant re-mints. */
  paramsKey: string;
}

export type LayerStatus = "idle" | "minting" | "ready" | "error";

export interface Layer {
  /** Unique instance id (one product can be added twice). */
  id: string;
  dataset: string;
  product: string;
  /** Display label resolved from the catalog at add time. */
  label: string;
  opacity: number;
  visible: boolean;
  vizOverrides: VizOverrides | null;
  /** Data-adaptive vis range from the composite's percentiles (server-side). */
  autoRange: boolean;
  mint: LayerMint | null;
  status: LayerStatus;
  error: string | null;
}

interface LayersState {
  /** Bottom-most layer first (matches MapLibre z-order). */
  layers: Layer[];
  addLayer(dataset: string, product: string, label: string): string;
  removeLayer(id: string): void;
  setOpacity(id: string, opacity: number): void;
  toggleVisible(id: string): void;
  toggleAutoRange(id: string): void;
  /** Move a layer one step up (+1, toward the viewer) or down (−1). */
  moveLayer(id: string, direction: 1 | -1): void;
  setMinting(id: string): void;
  setMint(id: string, mint: LayerMint): void;
  setError(id: string, error: string): void;
}

let nextId = 1;

function patch(layers: Layer[], id: string, changes: Partial<Layer>): Layer[] {
  return layers.map((layer) => (layer.id === id ? { ...layer, ...changes } : layer));
}

export const useLayersStore = create<LayersState>()((set) => ({
  layers: [],

  addLayer: (dataset, product, label) => {
    const id = `L${nextId++}`;
    set((state) => ({
      layers: [
        ...state.layers,
        {
          id,
          dataset,
          product,
          label,
          opacity: 0.8,
          visible: true,
          vizOverrides: null,
          autoRange: false,
          mint: null,
          status: "idle",
          error: null,
        },
      ],
    }));
    return id;
  },

  removeLayer: (id) =>
    set((state) => ({ layers: state.layers.filter((layer) => layer.id !== id) })),

  setOpacity: (id, opacity) => set((state) => ({ layers: patch(state.layers, id, { opacity }) })),

  toggleVisible: (id) =>
    set((state) => ({
      layers: state.layers.map((layer) =>
        layer.id === id ? { ...layer, visible: !layer.visible } : layer,
      ),
    })),

  toggleAutoRange: (id) =>
    set((state) => ({
      layers: state.layers.map((layer) =>
        layer.id === id ? { ...layer, autoRange: !layer.autoRange } : layer,
      ),
    })),

  moveLayer: (id, direction) =>
    set((state) => {
      const index = state.layers.findIndex((layer) => layer.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= state.layers.length) return state;
      const layers = [...state.layers];
      const [moved] = layers.splice(index, 1);
      layers.splice(target, 0, moved!);
      return { layers };
    }),

  setMinting: (id) =>
    set((state) => ({ layers: patch(state.layers, id, { status: "minting", error: null }) })),

  setMint: (id, mint) =>
    set((state) => ({ layers: patch(state.layers, id, { mint, status: "ready", error: null }) })),

  setError: (id, error) =>
    set((state) => ({ layers: patch(state.layers, id, { status: "error", error }) })),
}));
