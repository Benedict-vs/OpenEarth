/**
 * Encode an ERA5 u/v wind field (the /wind/field JSON grid) into an RGBA texture
 * for GPU sampling: R = u, G = v, each linearly rescaled from [min, max] → [0, 255].
 * The shader recovers velocity with `mix(windMin, windMax, rgba.rg)`. Masked cells
 * encode zero velocity. Pure — unit-tested for the round-trip within quantization.
 */
import type { WindField } from "../../api/types";

export interface WindTexture {
  width: number;
  height: number;
  data: Uint8Array; // nx*ny RGBA, row-major from the NW corner (grid order)
  windMin: [number, number]; // [uMin, vMin]
  windMax: [number, number]; // [uMax, vMax]
}

function encode(value: number, lo: number, hi: number): number {
  const span = hi - lo;
  if (span < 1e-9) return 0;
  return Math.round(255 * Math.min(1, Math.max(0, (value - lo) / span)));
}

export function buildWindTexture(field: WindField): WindTexture {
  const { nx, ny, u, v } = field;
  let uMin = Infinity;
  let uMax = -Infinity;
  let vMin = Infinity;
  let vMax = -Infinity;
  for (let i = 0; i < u.length; i++) {
    const cu = u[i];
    const cv = v[i];
    if (cu != null) {
      uMin = Math.min(uMin, cu);
      uMax = Math.max(uMax, cu);
    }
    if (cv != null) {
      vMin = Math.min(vMin, cv);
      vMax = Math.max(vMax, cv);
    }
  }
  // All-masked (or degenerate) field: fall back to a symmetric ±1 range so the
  // encoder is well-defined and zero velocity maps to the mid-point.
  if (!Number.isFinite(uMin)) [uMin, uMax] = [-1, 1];
  if (!Number.isFinite(vMin)) [vMin, vMax] = [-1, 1];

  const data = new Uint8Array(nx * ny * 4);
  for (let i = 0; i < nx * ny; i++) {
    const cu = u[i];
    const cv = v[i];
    const j = i * 4;
    data[j] = encode(cu ?? 0, uMin, uMax);
    data[j + 1] = encode(cv ?? 0, vMin, vMax);
    data[j + 2] = 0;
    data[j + 3] = 255;
  }
  return { width: nx, height: ny, data, windMin: [uMin, vMin], windMax: [uMax, vMax] };
}
