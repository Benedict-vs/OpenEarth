/** Small geodetic helpers (pure, no map dependency). */

/** Metres per degree of latitude (WGS84 mean) — good enough for tiny AOIs. */
const M_PER_DEG = 111_320;

export interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

/**
 * An axis-aligned bbox of ±(`halfPixels` × `scaleM`) around a point — the
 * pixel inspector's mini time-series ROI. Longitude degrees widen by
 * 1/cos(lat) so the box stays roughly square on the ground away from the
 * equator.
 */
export function pointBBox(lon: number, lat: number, scaleM: number, halfPixels = 10): Bounds {
  const halfM = halfPixels * scaleM;
  const dLat = halfM / M_PER_DEG;
  const dLon = halfM / (M_PER_DEG * Math.cos((lat * Math.PI) / 180));
  return { west: lon - dLon, south: lat - dLat, east: lon + dLon, north: lat + dLat };
}
