import { describe, expect, it } from "vitest";
import type { Dataset } from "../api/types";
import {
  boundsCaption,
  clampDate,
  clampPeriod,
  productBounds,
  rangeOutside,
  unionBounds,
} from "./coverage";

const CATALOG = [
  {
    id: "s5p",
    products: [
      { key: "NO2", available_from: "2018-06-28", available_to: null },
      { key: "CH4", available_from: "2019-02-08", available_to: null },
    ],
  },
  {
    id: "emit",
    products: [{ key: "CH4ENH", available_from: "2022-08-10", available_to: "2024-11-30" }],
  },
  { id: "custom", products: [{ key: "X" }] },
] as unknown as Dataset[];

const EMIT = { min: "2022-08-10", max: "2024-11-30" };

describe("productBounds", () => {
  it("reads the product's effective coverage", () => {
    expect(productBounds(CATALOG, "s5p", "CH4")).toEqual({ min: "2019-02-08", max: undefined });
    expect(productBounds(CATALOG, "emit", "CH4ENH")).toEqual(EMIT);
  });

  it("is unbounded while loading or for user datasets without coverage", () => {
    expect(productBounds(undefined, "s5p", "CH4")).toEqual({});
    expect(productBounds(CATALOG, "custom", "X")).toEqual({ min: undefined, max: undefined });
  });
});

describe("unionBounds", () => {
  it("takes the earliest start and the latest end", () => {
    expect(unionBounds([{ min: "2019-02-08" }, EMIT])).toEqual({
      min: "2019-02-08",
      max: undefined,
    });
    expect(unionBounds([EMIT, { min: "2015-01-01", max: "2020-01-01" }])).toEqual({
      min: "2015-01-01",
      max: "2024-11-30",
    });
  });

  it("an unbounded member (or no members) leaves the side open", () => {
    expect(unionBounds([EMIT, {}])).toEqual({ min: undefined, max: undefined });
    expect(unionBounds([])).toEqual({});
  });
});

describe("clampDate / clampPeriod", () => {
  it("clamps a pre-launch date to the first granule", () => {
    expect(clampDate("2017-01-01", { min: "2018-06-28" })).toBe("2018-06-28");
    expect(clampDate("2026-01-01", EMIT)).toBe("2024-11-30");
    expect(clampDate("2023-01-01", EMIT)).toBe("2023-01-01");
  });

  it("returns the same object when already inside", () => {
    const p = { start: "2023-01-01", end: "2023-06-01" };
    expect(clampPeriod(p, EMIT)).toBe(p);
  });

  it("trims a straddling period", () => {
    expect(clampPeriod({ start: "2017-01-01", end: "2019-01-01" }, { min: "2018-06-28" })).toEqual({
      start: "2018-06-28",
      end: "2019-01-01",
    });
  });

  it("keeps a wholly-outside period's length instead of collapsing it", () => {
    expect(clampPeriod({ start: "2016-01-01", end: "2016-01-31" }, { min: "2018-06-28" })).toEqual({
      start: "2018-06-28",
      end: "2018-07-28",
    });
    expect(clampPeriod({ start: "2026-01-01", end: "2026-01-31" }, EMIT)).toEqual({
      start: "2024-10-31",
      end: "2024-11-30",
    });
  });
});

describe("rangeOutside / boundsCaption", () => {
  it("flags ranges with no overlap only", () => {
    expect(rangeOutside("2016-01-01", "2016-02-01", EMIT)).toBe(true);
    expect(rangeOutside("2025-01-01", "2025-02-01", EMIT)).toBe(true);
    expect(rangeOutside("2022-08-01", "2022-08-20", EMIT)).toBe(false);
    expect(rangeOutside("1900-01-01", "1900-01-02", {})).toBe(false);
  });

  it("captions each bound shape", () => {
    expect(boundsCaption({ min: "2018-06-28" })).toBe("Data from 2018-06-28");
    expect(boundsCaption(EMIT)).toBe("Data 2022-08-10 – 2024-11-30");
    expect(boundsCaption({})).toBeNull();
  });
});
