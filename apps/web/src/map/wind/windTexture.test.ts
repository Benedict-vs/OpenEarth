import { describe, expect, it } from "vitest";
import type { WindField } from "../../api/types";
import { buildWindTexture } from "./windTexture";

function field(u: (number | null)[], v: (number | null)[], nx: number, ny: number): WindField {
  return {
    bbox: { kind: "bbox", west: 0, south: 0, east: 1, north: 1 },
    nx,
    ny,
    u,
    v,
    when: "2024-06-01T12:00:00Z",
    collection_id: "ERA5",
  } as WindField;
}

/** Recover velocity the way the shader does: mix(min, max, rg/255). */
function decode(tex: ReturnType<typeof buildWindTexture>, i: number): [number, number] {
  const r = (tex.data[i * 4] ?? 0) / 255;
  const g = (tex.data[i * 4 + 1] ?? 0) / 255;
  const [uMin, vMin] = tex.windMin;
  const [uMax, vMax] = tex.windMax;
  return [uMin + r * (uMax - uMin), vMin + g * (vMax - vMin)];
}

describe("buildWindTexture", () => {
  it("round-trips u/v within quantization error", () => {
    const u = [-8, -2, 3, 9];
    const v = [1, 5, -4, 7];
    const tex = buildWindTexture(field(u, v, 2, 2));
    expect(tex.width).toBe(2);
    expect(tex.height).toBe(2);
    expect(tex.data.length).toBe(2 * 2 * 4);
    const uQ = (tex.windMax[0] - tex.windMin[0]) / 255;
    const vQ = (tex.windMax[1] - tex.windMin[1]) / 255;
    for (let i = 0; i < 4; i++) {
      const [du, dv] = decode(tex, i);
      expect(Math.abs(du - u[i]!)).toBeLessThanOrEqual(uQ + 1e-9);
      expect(Math.abs(dv - v[i]!)).toBeLessThanOrEqual(vQ + 1e-9);
    }
  });

  it("tracks the min/max range from the data", () => {
    const tex = buildWindTexture(field([-8, 9], [1, 7], 2, 1));
    expect(tex.windMin).toEqual([-8, 1]);
    expect(tex.windMax).toEqual([9, 7]);
    // Extremes map to the byte endpoints.
    expect(tex.data[0]).toBe(0); // u = -8 (min)
    expect(tex.data[4]).toBe(255); // u = 9 (max)
    expect(tex.data[3]).toBe(255); // alpha
  });

  it("encodes masked cells as zero velocity (range spans 0)", () => {
    // Range must straddle 0 for zero to be representable; masked cell decodes to ~0.
    const tex = buildWindTexture(field([null, -3, 5], [null, -2, 6], 3, 1));
    const [du, dv] = decode(tex, 0);
    const uQ = (tex.windMax[0] - tex.windMin[0]) / 255;
    const vQ = (tex.windMax[1] - tex.windMin[1]) / 255;
    expect(Math.abs(du)).toBeLessThanOrEqual(uQ + 1e-9);
    expect(Math.abs(dv)).toBeLessThanOrEqual(vQ + 1e-9);
  });

  it("handles an all-masked field without NaNs", () => {
    const tex = buildWindTexture(field([null, null], [null, null], 2, 1));
    expect(tex.windMin.every(Number.isFinite)).toBe(true);
    expect(tex.windMax.every(Number.isFinite)).toBe(true);
    expect([...tex.data].every((b) => Number.isFinite(b))).toBe(true);
  });
});
