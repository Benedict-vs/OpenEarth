import { describe, expect, it } from "vitest";
import type { TimelapseManifest } from "../api/types";
import { coverageSummary, frameQc, pct, sourceKind } from "./manifest";

function manifest(): TimelapseManifest {
  return {
    dataset: "s2",
    product: "RGB",
    width: 400,
    height: 300,
    vis: [0, 0.3],
    cancelled: false,
    composite: "clearest",
    post: { gap_fill: true, fallback_source: "hls" },
    frames: [
      { index: 0, start: "2024-01-01", end: "2024-01-31", label: "Jan", status: "rendered", source: "s2", valid_fraction: 1.0, filled_fraction: 0 },
      { index: 1, start: "2024-02-01", end: "2024-02-29", label: "Feb", status: "rendered", source: "s2", valid_fraction: 0.9, filled_fraction: 0.05 },
      { index: null, start: "2024-03-01", end: "2024-03-31", label: "Mar", status: "empty", source: null, valid_fraction: null, filled_fraction: null },
      { index: 2, start: "2024-04-01", end: "2024-04-30", label: "Apr", status: "rendered", source: "hls", valid_fraction: 0.8, filled_fraction: 0.1 },
    ],
  };
}

describe("manifest readers", () => {
  it("looks up per-frame QC by dense movie index", () => {
    expect(frameQc(manifest(), 1)).toEqual({ source: "s2", valid: 0.9, filled: 0.05, label: "Feb" });
    expect(frameQc(manifest(), 99)).toBeNull();
    expect(frameQc(null, 0)).toBeNull();
  });

  it("classifies a frame's source relative to the primary dataset", () => {
    expect(sourceKind("s2", "s2")).toBe("primary");
    expect(sourceKind("hls", "s2")).toBe("fallback");
    expect(sourceKind(null, "s2")).toBe("gap");
  });

  it("summarises coverage across rendered frames", () => {
    const s = coverageSummary(manifest());
    expect(s.windows).toBe(4);
    expect(s.rendered).toBe(3);
    expect(s.empty).toBe(1);
    expect(s.borrowed).toBe(2); // Feb + Apr had filled pixels
    expect(s.sources).toEqual({ s2: 2, hls: 1 });
    expect(s.meanValid).toBeCloseTo((1.0 + 0.9 + 0.8) / 3);
  });

  it("formats fractions as percentages", () => {
    expect(pct(0.962)).toBe("96%");
    expect(pct(null)).toBe("—");
    expect(pct(0.5, 1)).toBe("50.0%");
  });
});
