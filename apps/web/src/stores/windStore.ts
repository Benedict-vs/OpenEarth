import { create } from "zustand";

/** Whether the ERA5 wind-arrow overlay is drawn over the map. */
interface WindState {
  enabled: boolean;
  toggle(): void;
  setEnabled(enabled: boolean): void;
}

export const useWindStore = create<WindState>()((set) => ({
  enabled: false,
  toggle: () => set((s) => ({ enabled: !s.enabled })),
  setEnabled: (enabled) => set({ enabled }),
}));
