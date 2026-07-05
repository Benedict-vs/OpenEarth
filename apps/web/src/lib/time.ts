/** Pure time/expiry helpers (unit-tested). */

export const REMINT_FRACTION = 0.75;

/** Epoch ms at which a mint should be refreshed (75 % of its lifetime). */
export function remintAtMs(
  mintedAtMs: number,
  expiresAtMs: number,
  fraction: number = REMINT_FRACTION,
): number {
  return mintedAtMs + Math.max(0, expiresAtMs - mintedAtMs) * fraction;
}

export function isoToMs(iso: string): number {
  return new Date(iso).getTime();
}

/** "1h 23m" / "4m 05s" style countdown for the layer panel badge. */
export function formatCountdown(ms: number): string {
  if (ms <= 0) return "expired";
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${m.toString().padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${s.toString().padStart(2, "0")}s`;
  return `${s}s`;
}

/** Default date range for new sessions: the last 30 days. */
export function defaultDateRange(now: Date = new Date()): { start: string; end: string } {
  const end = now.toISOString().slice(0, 10);
  const start = new Date(now.getTime() - 30 * 24 * 3600 * 1000).toISOString().slice(0, 10);
  return { start, end };
}
