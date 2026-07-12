import { describe, expect, it } from "vitest";
import type { RoiIn } from "../api/types";
import {
  advanceFrame,
  dateAxis,
  evictableKeys,
  type FrameStatus,
  imageSourceCorners,
  poolIndices,
  roiEnvelope,
} from "./animation";

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

describe("advanceFrame (buffer-aware transport step)", () => {
  const s = (m: Record<number, FrameStatus>): Record<number, FrameStatus> => m;

  it("holds on a single (or empty) frame", () => {
    expect(advanceFrame({}, 0, 1)).toBe(0);
    expect(advanceFrame({}, 0, 0)).toBe(0);
  });

  it("advances when the next frame is ready", () => {
    expect(advanceFrame(s({ 0: "ready", 1: "ready" }), 0, 3)).toBe(1);
  });

  it("holds on the current frame when the next is still minting", () => {
    expect(advanceFrame(s({ 0: "ready", 1: "minting" }), 0, 3)).toBe(0);
  });

  it("holds when the next frame has not been requested yet (undefined)", () => {
    expect(advanceFrame(s({ 0: "ready" }), 0, 3)).toBe(0);
  });

  it("skips a permanently-failed frame to reach the next ready one", () => {
    expect(advanceFrame(s({ 0: "ready", 1: "error", 2: "ready" }), 0, 3)).toBe(2);
  });

  it("holds after skipping an error when the one beyond is still loading", () => {
    expect(advanceFrame(s({ 0: "ready", 1: "error", 2: "minting" }), 0, 3)).toBe(0);
  });

  it("wraps to a ready frame 0 from the last index", () => {
    expect(advanceFrame(s({ 0: "ready", 1: "ready", 2: "ready" }), 2, 3)).toBe(0);
  });

  it("holds at the end when frame 0 is not ready (no premature wrap)", () => {
    expect(advanceFrame(s({ 0: "minting", 1: "ready", 2: "ready" }), 2, 3)).toBe(2);
  });

  it("never deadlocks on an all-error pool — it holds", () => {
    expect(advanceFrame(s({ 0: "error", 1: "error", 2: "error" }), 1, 3)).toBe(1);
  });
});
