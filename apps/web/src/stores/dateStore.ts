import { create } from "zustand";
import { defaultDateRange } from "../lib/time";

export type DateMode = "range" | "single";

interface DateState {
  mode: DateMode;
  /** ISO dates for range mode. */
  start: string;
  end: string;
  /** ISO date for single-date (date-window) mode. */
  targetDate: string;
  halfWindowDays: number;
  setMode(mode: DateMode): void;
  setRange(start: string, end: string): void;
  setTargetDate(targetDate: string): void;
  setHalfWindowDays(days: number): void;
}

const initial = defaultDateRange();

export const useDateStore = create<DateState>()((set) => ({
  mode: "range",
  start: initial.start,
  end: initial.end,
  targetDate: initial.end,
  halfWindowDays: 3,
  setMode: (mode) => set({ mode }),
  setRange: (start, end) => set({ start, end }),
  setTargetDate: (targetDate) => set({ targetDate }),
  setHalfWindowDays: (halfWindowDays) => set({ halfWindowDays }),
}));
