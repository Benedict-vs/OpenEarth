import { beforeEach, describe, expect, it } from "vitest";
import { boundsToBBox, ringToBBox, ringToPolygon, roiBounds, useRoiStore } from "./roiStore";

describe("roi conversions", () => {
  it("converts a drawn rectangle ring to an axis-aligned bbox", () => {
    // Closed ring, arbitrary winding/start corner.
    const ring: [number, number][] = [
      [8.8, 49.3],
      [8.5, 49.3],
      [8.5, 49.6],
      [8.8, 49.6],
      [8.8, 49.3],
    ];
    expect(ringToBBox(ring)).toEqual({
      kind: "bbox",
      west: 8.5,
      south: 49.3,
      east: 8.8,
      north: 49.6,
    });
  });

  it("keeps polygon rings verbatim (closed rings accepted by the API)", () => {
    const ring: [number, number][] = [
      [8.6, 49.3],
      [8.8, 49.35],
      [8.7, 49.5],
      [8.6, 49.3],
    ];
    const roi = ringToPolygon(ring);
    expect(roi).toEqual({ kind: "polygon", coordinates: ring });
  });

  it("computes fit bounds for both ROI kinds", () => {
    expect(roiBounds(boundsToBBox(1, 2, 3, 4))).toEqual([1, 2, 3, 4]);
    expect(
      roiBounds(
        ringToPolygon([
          [8.6, 49.3],
          [8.8, 49.35],
          [8.7, 49.5],
        ]),
      ),
    ).toEqual([8.6, 49.3, 8.8, 49.5]);
  });
});

describe("roiStore", () => {
  beforeEach(() => {
    useRoiStore.setState({ roi: null, presetName: null });
  });

  it("drawn ROIs clear any preset attribution", () => {
    const { applyPreset, setRoi } = useRoiStore.getState();
    applyPreset("Europe", boundsToBBox(-25, 34, 45, 72));
    expect(useRoiStore.getState().presetName).toBe("Europe");
    setRoi(boundsToBBox(8, 49, 9, 50));
    expect(useRoiStore.getState().presetName).toBeNull();
  });

  it("clear() returns to the whole-globe state", () => {
    useRoiStore.getState().setRoi(boundsToBBox(8, 49, 9, 50));
    useRoiStore.getState().clear();
    expect(useRoiStore.getState().roi).toBeNull();
  });
});
