import { create } from "zustand";

/** The workspace the current view was last saved as or loaded from — lets the
 *  header "Update" action target it, and shows which one is active. Null means
 *  the view is unsaved / detached from any stored workspace. */
interface WorkspaceState {
  currentId: number | null;
  currentName: string | null;
  setCurrent(id: number, name: string): void;
  clear(): void;
}

export const useWorkspaceStore = create<WorkspaceState>()((set) => ({
  currentId: null,
  currentName: null,
  setCurrent: (currentId, currentName) => set({ currentId, currentName }),
  clear: () => set({ currentId: null, currentName: null }),
}));
