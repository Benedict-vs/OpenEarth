/**
 * Archive coverage → date-picker bounds (pure, unit-tested).
 *
 * The catalog serves each product's effective inclusive coverage
 * (`available_from`/`available_to`, `null` = unbounded). Pickers use these as
 * `min`/`max` AND clamp — a native date input's `min` doesn't stop typed or
 * programmatic values (presets, workspace restore, a product switch).
 */
import type { Dataset } from "../api/types";
import { addDays, type Period } from "./timeWindow";

export interface DateBounds {
  /** Earliest selectable ISO date; undefined = unbounded. */
  min?: string | undefined;
  /** Latest selectable ISO date; undefined = unbounded. */
  max?: string | undefined;
}

/** Bounds for one product, or {} while the catalog loads / for an unknown product. */
export function productBounds(
  catalog: Dataset[] | undefined,
  dataset: string,
  product: string,
): DateBounds {
  const spec = catalog?.find((d) => d.id === dataset)?.products.find((p) => p.key === product);
  return {
    min: spec?.available_from ?? undefined,
    max: spec?.available_to ?? undefined,
  };
}

/**
 * The span where AT LEAST ONE of several products has data (Explore's window is
 * shared across layers). Any unbounded side leaves that side unbounded.
 */
export function unionBounds(all: DateBounds[]): DateBounds {
  if (all.length === 0) return {};
  const mins = all.map((b) => b.min);
  const maxes = all.map((b) => b.max);
  return {
    min: mins.some((m) => m === undefined) ? undefined : mins.sort()[0],
    max: maxes.some((m) => m === undefined) ? undefined : maxes.sort().at(-1),
  };
}

export function clampDate(iso: string, b: DateBounds): string {
  if (b.min && iso < b.min) return b.min;
  if (b.max && iso > b.max) return b.max;
  return iso;
}

/**
 * Clamp both ends into bounds. A period lying wholly outside would collapse to
 * a single day, so it keeps its length from the bound instead (still clamped).
 */
export function clampPeriod(p: Period, b: DateBounds): Period {
  const start = clampDate(p.start, b);
  const end = clampDate(p.end, b);
  if (start === p.start && end === p.end) return p;
  if (start !== end || p.start === p.end) return { start, end };
  const spanDays = Math.round(
    (new Date(`${p.end}T00:00:00Z`).getTime() - new Date(`${p.start}T00:00:00Z`).getTime()) /
      86_400_000,
  );
  if (b.min && p.end < b.min) return { start, end: clampDate(addDays(start, spanDays), b) };
  return { start: clampDate(addDays(end, -spanDays), b), end };
}

/** True when [start, end] has no overlap with the bounds (nothing to render). */
export function rangeOutside(start: string, end: string, b: DateBounds): boolean {
  return (!!b.min && end < b.min) || (!!b.max && start > b.max);
}

/** Short availability caption, e.g. "Data from 2018-06-28" / "Data 2022-08-10 – 2024-11-30". */
export function boundsCaption(b: DateBounds): string | null {
  if (b.min && b.max) return `Data ${b.min} – ${b.max}`;
  if (b.min) return `Data from ${b.min}`;
  if (b.max) return `Data until ${b.max}`;
  return null;
}
