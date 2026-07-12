import { describe, expect, it } from "vitest";
import {
  addDays,
  clampHalfDays,
  defaultPeriod,
  defaultWindow,
  formatWindowCaption,
  rangeToWindow,
  windowMeanDates,
  windowRange,
} from "./timeWindow";

// A fixed "now" so the today-clamp is deterministic.
const NOW = new Date("2026-07-12T09:00:00Z");

describe("windowRange", () => {
  it("is the inclusive [center-h, center+h] range", () => {
    expect(windowRange({ center: "2026-06-12", halfDays: 15 }, NOW)).toEqual({
      start: "2026-05-28",
      end: "2026-06-27",
    });
  });

  it("clamps the end to today for a future-leaning window", () => {
    expect(windowRange({ center: "2026-07-10", halfDays: 15 }, NOW)).toEqual({
      start: "2026-06-25",
      end: "2026-07-12", // center+15 = 2026-07-25, clamped to today
    });
  });

  it("is a single day for a ±0 window", () => {
    expect(windowRange({ center: "2026-03-01", halfDays: 0 }, NOW)).toEqual({
      start: "2026-03-01",
      end: "2026-03-01",
    });
  });
});

describe("windowMeanDates (the wire request window)", () => {
  it("has an exclusive end one day past the display end", () => {
    expect(windowMeanDates({ center: "2026-06-12", halfDays: 15 }, NOW)).toEqual({
      start: "2026-05-28",
      end: "2026-06-28", // 2026-06-27 + 1, exclusive
    });
  });

  it("is exactly one calendar day for ±0 (matches build_date_composite)", () => {
    expect(windowMeanDates({ center: "2026-03-01", halfDays: 0 }, NOW)).toEqual({
      start: "2026-03-01",
      end: "2026-03-02",
    });
  });

  it("clamps the display end to today before adding the exclusive day", () => {
    expect(windowMeanDates({ center: "2026-07-10", halfDays: 15 }, NOW)).toEqual({
      start: "2026-06-25",
      end: "2026-07-13", // today (2026-07-12) + 1
    });
  });
});

describe("rangeToWindow (v1 migration primitive)", () => {
  it("round-trips an even-width range through windowRange", () => {
    const w = rangeToWindow("2026-05-28", "2026-06-27");
    expect(w).toEqual({ center: "2026-06-12", halfDays: 15 });
    // A far-past window is never clamped, so it inverts exactly.
    expect(windowRange(w, NOW)).toEqual({ start: "2026-05-28", end: "2026-06-27" });
  });

  it("uses ceil(span/2) for an odd-width range", () => {
    // 2024-03-01 → 2024-09-01 is 184 days.
    const w = rangeToWindow("2024-03-01", "2024-09-01");
    expect(w).toEqual({ center: "2024-06-01", halfDays: 92 });
  });

  it("maps a zero-width range to a ±0 window", () => {
    expect(rangeToWindow("2024-06-15", "2024-06-15")).toEqual({
      center: "2024-06-15",
      halfDays: 0,
    });
  });
});

describe("formatWindowCaption", () => {
  it("is the pinned caption string", () => {
    expect(formatWindowCaption({ center: "2026-06-12", halfDays: 15 }, NOW)).toBe(
      "≙ 2026-05-28 → 2026-06-27 · mean composite, clouds masked",
    );
  });
});

describe("clampHalfDays", () => {
  it("clamps to [0, 183] and rounds", () => {
    expect(clampHalfDays(-4)).toBe(0);
    expect(clampHalfDays(200)).toBe(183);
    expect(clampHalfDays(3.6)).toBe(4);
    expect(clampHalfDays(Number.NaN)).toBe(0);
  });
});

describe("defaults", () => {
  it("defaultWindow is today − 15 d, ±15 d", () => {
    expect(defaultWindow(NOW)).toEqual({ center: "2026-06-27", halfDays: 15 });
  });

  it("defaultPeriod is the last 12 months", () => {
    expect(defaultPeriod(NOW)).toEqual({ start: "2025-07-12", end: "2026-07-12" });
  });

  it("addDays wraps month boundaries", () => {
    expect(addDays("2026-01-31", 1)).toBe("2026-02-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });
});
