import { create } from "zustand";

/**
 * Which finished render (if any) is playing on the Explore map. Set from the
 * gallery's "Play on map"; the Explore PlaybackBar reads it and overlays the
 * render's frames. `null` = no playback docked.
 */
interface PlaybackState {
  renderId: string | null;
  setRenderId(renderId: string | null): void;
}

export const usePlaybackStore = create<PlaybackState>()((set) => ({
  renderId: null,
  setRenderId: (renderId) => set({ renderId }),
}));
