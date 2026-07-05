/** Pure time-series helpers for the analysis panel (unit-tested). */

export interface SeriesPoint {
  /** ISO date, e.g. "2019-04-02". */
  date: string;
  value: number;
  count: number;
}

const MS_PER_DAY = 86_400_000;

/**
 * Merge the coarse preview and the native (fine) series: the fine value wins
 * wherever it exists, else the coarse value shows through. Result is sorted by
 * date. This is the render rule that makes fine chunks visibly replace the
 * coarse preview as they land.
 */
export function mergeCoarseFine(
  coarse: ReadonlyMap<string, SeriesPoint>,
  fine: ReadonlyMap<string, SeriesPoint>,
): SeriesPoint[] {
  const byDate = new Map<string, SeriesPoint>(coarse);
  for (const [date, point] of fine) byDate.set(date, point);
  return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
}

/**
 * Centered rolling mean over a *calendar-day* window (default 7 → ±3 days),
 * aligned to `points`. Averaging by date, not by index, keeps a sparse series
 * (missing days) from over-smoothing. Returns `null` where no point falls in
 * the window (only possible for an empty input).
 */
export function rollingMean(points: SeriesPoint[], windowDays = 7): (number | null)[] {
  const half = Math.floor(windowDays / 2);
  const times = points.map((p) => Date.parse(p.date));
  return points.map((_, i) => {
    let sum = 0;
    let n = 0;
    for (let j = 0; j < points.length; j++) {
      if (Math.abs(times[j]! - times[i]!) <= half * MS_PER_DAY) {
        sum += points[j]!.value;
        n += 1;
      }
    }
    return n > 0 ? sum / n : null;
  });
}

export interface SeriesStats {
  n: number;
  mean: number;
  std: number;
  min: SeriesPoint;
  max: SeriesPoint;
  /** Least-squares slope in value-units per year. */
  trendPerYear: number;
  /** Fraction of the requested window that has a data point (n / range-days). */
  coverage: number;
  rangeDays: number;
}

/**
 * Descriptive statistics of a displayed series over its request window.
 * `std` is the population standard deviation (divide by n) so a single point
 * gives 0 rather than NaN. Returns `null` for an empty series.
 */
export function seriesStats(
  points: SeriesPoint[],
  rangeStart: string,
  rangeEnd: string,
): SeriesStats | null {
  const n = points.length;
  if (n === 0) return null;

  const values = points.map((p) => p.value);
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / n;

  let min = points[0]!;
  let max = points[0]!;
  for (const p of points) {
    if (p.value < min.value) min = p;
    if (p.value > max.value) max = p;
  }

  const t0 = Date.parse(points[0]!.date);
  const days = points.map((p) => (Date.parse(p.date) - t0) / MS_PER_DAY);
  const trendPerYear = leastSquaresSlope(days, values) * 365.25;

  const rangeDays = Math.max(
    1,
    Math.round((Date.parse(rangeEnd) - Date.parse(rangeStart)) / MS_PER_DAY),
  );

  return {
    n,
    mean,
    std: Math.sqrt(variance),
    min,
    max,
    trendPerYear,
    coverage: n / rangeDays,
    rangeDays,
  };
}

/** OLS slope of y on x; 0 when x has no spread (n < 2 or all-equal x). */
function leastSquaresSlope(xs: number[], ys: number[]): number {
  const n = xs.length;
  const sumX = xs.reduce((a, b) => a + b, 0);
  const sumY = ys.reduce((a, b) => a + b, 0);
  const sumXY = xs.reduce((acc, x, i) => acc + x * ys[i]!, 0);
  const sumXX = xs.reduce((acc, x) => acc + x * x, 0);
  const denom = n * sumXX - sumX * sumX;
  return denom === 0 ? 0 : (n * sumXY - sumX * sumY) / denom;
}
