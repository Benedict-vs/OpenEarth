import { describe, expect, it } from "vitest";
import { mergeCoarseFine, rollingMean, seriesStats, type SeriesPoint } from "./series";

function pt(date: string, value: number, count = 100): SeriesPoint {
  return { date, value, count };
}

function toMap(points: SeriesPoint[]): Map<string, SeriesPoint> {
  return new Map(points.map((p) => [p.date, p]));
}

describe("mergeCoarseFine", () => {
  it("prefers fine values and fills gaps with coarse, sorted by date", () => {
    const coarse = toMap([pt("2024-01-01", 1), pt("2024-01-08", 2), pt("2024-01-15", 3)]);
    const fine = toMap([pt("2024-01-08", 2.5)]); // one fine chunk landed
    const merged = mergeCoarseFine(coarse, fine);
    expect(merged.map((p) => [p.date, p.value])).toEqual([
      ["2024-01-01", 1],
      ["2024-01-08", 2.5], // fine wins
      ["2024-01-15", 3],
    ]);
  });

  it("returns fine-only dates too", () => {
    const merged = mergeCoarseFine(toMap([]), toMap([pt("2024-02-01", 9)]));
    expect(merged).toEqual([pt("2024-02-01", 9)]);
  });

  it("is empty for two empty maps", () => {
    expect(mergeCoarseFine(new Map(), new Map())).toEqual([]);
  });
});

describe("rollingMean", () => {
  it("centers a ±3-day window over consecutive days", () => {
    // Values 0..6 on consecutive days; the middle point averages all 7.
    const points = Array.from({ length: 7 }, (_, i) => pt(`2024-03-0${i + 1}`, i));
    const smooth = rollingMean(points, 7);
    expect(smooth[3]).toBeCloseTo(3); // mean of 0..6
    expect(smooth[0]).toBeCloseTo((0 + 1 + 2 + 3) / 4); // only days within +3
  });

  it("does not reach across a gap larger than the window", () => {
    const points = [pt("2024-01-01", 10), pt("2024-06-01", 20)];
    const smooth = rollingMean(points, 7);
    expect(smooth).toEqual([10, 20]); // each isolated
  });

  it("is empty for an empty series", () => {
    expect(rollingMean([], 7)).toEqual([]);
  });
});

describe("seriesStats", () => {
  it("computes mean, population std, min/max with dates, and coverage", () => {
    const points = [pt("2024-01-01", 2), pt("2024-01-02", 4), pt("2024-01-03", 6)];
    const stats = seriesStats(points, "2024-01-01", "2024-01-11")!;
    expect(stats.n).toBe(3);
    expect(stats.mean).toBeCloseTo(4);
    expect(stats.std).toBeCloseTo(Math.sqrt(((2 - 4) ** 2 + 0 + (6 - 4) ** 2) / 3));
    expect(stats.min).toMatchObject({ date: "2024-01-01", value: 2 });
    expect(stats.max).toMatchObject({ date: "2024-01-03", value: 6 });
    expect(stats.rangeDays).toBe(10);
    expect(stats.coverage).toBeCloseTo(0.3); // 3 / 10
  });

  it("reports a positive per-year trend for a rising line", () => {
    // +2 units/day over 3 days → 2 * 365.25 per year.
    const points = [pt("2024-01-01", 0), pt("2024-01-02", 2), pt("2024-01-03", 4)];
    const stats = seriesStats(points, "2024-01-01", "2024-01-03")!;
    expect(stats.trendPerYear).toBeCloseTo(2 * 365.25);
  });

  it("returns null for an empty series and 0 trend for a single point", () => {
    expect(seriesStats([], "2024-01-01", "2024-02-01")).toBeNull();
    const one = seriesStats([pt("2024-01-01", 5)], "2024-01-01", "2024-01-08")!;
    expect(one.trendPerYear).toBe(0);
    expect(one.std).toBe(0);
  });
});
