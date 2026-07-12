import { create } from "zustand";

/** Top-level views (the nav bar order). Owned by App via this store so a
 *  feature can request a view change without prop-drilling a callback. */
export type View = "explore" | "compare" | "methane" | "timelapse" | "embeddings" | "settings";

interface UiState {
  view: View;
  navigate(view: View): void;
}

export const useUiStore = create<UiState>()((set) => ({
  view: "explore",
  navigate: (view) => set({ view }),
}));
