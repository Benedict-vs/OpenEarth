import { create } from "zustand";
import {
  clampHalfDays,
  defaultPeriod,
  defaultWindow,
  type Period,
  type TimeWindow,
} from "../lib/timeWindow";

/**
 * The shared time model (Phase 8): a **window** (what a composite shows) and a
 * **period** (a span you chart / preview / search across). The old conflated
 * `mode`/`start`/`end`/`targetDate`/`halfWindowDays` shape is gone — center+width
 * is the only "what am I looking at" control, and the period is owned separately
 * by the features that scan time.
 */
interface DateState {
  window: TimeWindow;
  period: Period;
  setWindow(patch: Partial<TimeWindow>): void;
  setPeriod(start: string, end: string): void;
}

export const useDateStore = create<DateState>()((set) => ({
  window: defaultWindow(),
  period: defaultPeriod(),
  setWindow: (patch) =>
    set((s) => {
      const next = { ...s.window, ...patch };
      return { window: { ...next, halfDays: clampHalfDays(next.halfDays) } };
    }),
  setPeriod: (start, end) => set({ period: { start, end } }),
}));
