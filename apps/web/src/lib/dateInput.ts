/**
 * Typed date entry (pure — no React). The app's date fields commit on Enter or
 * blur instead of on every keystroke: a controlled `<input type="date">` emitted
 * intermediate years ("0002-…") while typing, which the archive clamp snapped
 * to the mission start, so a year could not be typed at all.
 */
import type { Period } from "./timeWindow";

const DAY_MS = 86_400_000;

function iso(y: number, m: number, d: number): string {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function daysInMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

function valid(y: number, m: number, d: number): string | null {
  if (!Number.isInteger(y) || y < 1000 || y > 9999) return null;
  if (!Number.isInteger(m) || m < 1 || m > 12) return null;
  if (!Number.isInteger(d) || d < 1 || d > daysInMonth(y, m)) return null;
  return iso(y, m, d);
}

/**
 * Parse what a person types into ISO `YYYY-MM-DD`, or null if it is not a real
 * calendar date. Accepts `2018-06-19`, `2018-6-9`, `2018/06/19`, `19.06.2018`
 * (day-first, as written in Germany), and `2018-06` (→ the 1st of the month).
 */
export function parseDateInput(text: string): string | null {
  const t = text.trim();
  let m = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/.exec(t);
  if (m) return valid(Number(m[1]), Number(m[2]), Number(m[3]));
  m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(t);
  if (m) return valid(Number(m[3]), Number(m[2]), Number(m[1]));
  m = /^(\d{4})[-/](\d{1,2})$/.exec(t);
  if (m) return valid(Number(m[1]), Number(m[2]), 1);
  return null;
}

export type DateUnit = "day" | "month" | "year";

/**
 * Step an ISO date. Month/year steps keep the day when it exists and otherwise
 * land on the month's last day (Jan 31 + 1 month → Feb 28/29), so repeated
 * stepping never drifts into the following month.
 */
export function stepDate(isoDate: string, unit: DateUnit, delta: number): string {
  const [y, mo, d] = isoDate.split("-").map(Number) as [number, number, number];
  if (unit === "day") {
    return new Date(Date.UTC(y, mo - 1, d) + delta * DAY_MS).toISOString().slice(0, 10);
  }
  const months = unit === "month" ? delta : 12 * delta;
  const total = y * 12 + (mo - 1) + months;
  const ny = Math.floor(total / 12);
  const nm = (total % 12) + 1;
  return iso(ny, nm, Math.min(d, daysInMonth(ny, nm)));
}

function spanDays(p: Period): number {
  return Math.round(
    (Date.parse(`${p.end}T00:00:00Z`) - Date.parse(`${p.start}T00:00:00Z`)) / DAY_MS,
  );
}

/**
 * Move a whole period: by its own length ("span", e.g. the next 3 months) or by
 * a calendar year (same dates, other year — the common "same season, last year").
 */
export function shiftPeriod(p: Period, by: "span" | "year", direction: 1 | -1): Period {
  if (by === "year") {
    return { start: stepDate(p.start, "year", direction), end: stepDate(p.end, "year", direction) };
  }
  const days = spanDays(p) * direction;
  return { start: stepDate(p.start, "day", days), end: stepDate(p.end, "day", days) };
}
