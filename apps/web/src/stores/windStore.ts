import { create } from "zustand";

/** Wind viz toggles: the 2D arrow overlay and the GPU particle layer (independent). */
interface WindState {
  enabled: boolean; // arrow overlay
  particlesEnabled: boolean; // GPU particle layer
  toggle(): void;
  setEnabled(enabled: boolean): void;
  toggleParticles(): void;
}

export const useWindStore = create<WindState>()((set) => ({
  enabled: false,
  particlesEnabled: false,
  toggle: () => set((s) => ({ enabled: !s.enabled })),
  setEnabled: (enabled) => set({ enabled }),
  toggleParticles: () => set((s) => ({ particlesEnabled: !s.particlesEnabled })),
}));
