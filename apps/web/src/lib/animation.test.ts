import { describe, expect, it } from "vitest";
import type { RoiIn } from "../api/types";
import { dateAxis, evictableKeys, imageSourceCorners, poolIndices, roiEnvelope } from "./animation";

describe("imageSourceCorners", () => {
  it("emits top-left, top-right, bottom-right, bottom-left", () => {
    const bbox = { west: 8, south: 49, east: 9, north: 50 };
    expect(imageSourceCorners(bbox)).toEqual([
      [8, 50],
      [9, 50],
      [9, 49],
      [8, 49],
    ]);
  });
});

describe("roiEnvelope", () => {
  it("passes a bbox through", () => {
    const roi: RoiIn = { kind: "bbox", west: 1, south: 2, east: 3, north: 4 };
    expect(roiEnvelope(roi)).toEqual({ west: 1, south: 2, east: 3, north: 4 });
  });
  it("envelopes a polygon", () => {
    const roi: RoiIn = {
      kind: "polygon",
      coordinates: [
        [1, 2],
        [5, 3],
        [4, 8],
      ],
    };
    expect(roiEnvelope(roi)).toEqual({ west: 1, south: 2, east: 5, north: 8 });
  });
});

describe("dateAxis", () => {
  it("evenly spans the range, inclusive", () => {
    const axis = dateAxis("2024-01-01", "2024-01-11", 3);
    expect(axis).toEqual(["2024-01-01", "2024-01-06", "2024-01-11"]);
  });
  it("clamps to a single date on an invalid range", () => {
    expect(dateAxis("2024-02-01", "2024-01-01", 5)).toEqual(["2024-02-01"]);
  });
});

describe("poolIndices / evictableKeys", () => {
  it("keeps a ±radius clamped window", () => {
    expect(poolIndices(0, 10, 2)).toEqual([0, 1, 2]);
    expect(poolIndices(5, 10, 2)).toEqual([3, 4, 5, 6, 7]);
    expect(poolIndices(9, 10, 2)).toEqual([7, 8, 9]);
  });
  it("evicts loaded keys outside the pool", () => {
    const keep = poolIndices(5, 10, 2);
    expect(evictableKeys([0, 3, 5, 8], keep).sort((a, b) => a - b)).toEqual([0, 8]);
  });
});
