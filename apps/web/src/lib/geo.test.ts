import { describe, expect, it } from "vitest";
import { pointBBox } from "./geo";

describe("pointBBox", () => {
  it("is centred on the point", () => {
    const b = pointBBox(8.68, 49.41, 100);
    expect((b.west + b.east) / 2).toBeCloseTo(8.68, 10);
    expect((b.south + b.north) / 2).toBeCloseTo(49.41, 10);
  });

  it("widens longitude by 1/cos(lat)", () => {
    // At 60° latitude cos = 0.5, so the longitude span is twice the latitude span.
    const b = pointBBox(0, 60, 100);
    const lonSpan = b.east - b.west;
    const latSpan = b.north - b.south;
    expect(lonSpan / latSpan).toBeCloseTo(2, 6);
  });

  it("scales linearly with pixel size and half-width", () => {
    const small = pointBBox(0, 0, 100);
    const big = pointBBox(0, 0, 1000);
    expect(big.east - big.west).toBeCloseTo((small.east - small.west) * 10, 6);

    const wide = pointBBox(0, 0, 100, 20);
    expect(wide.north - wide.south).toBeCloseTo((small.north - small.south) * 2, 6);
  });
});
