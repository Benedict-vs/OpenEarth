/**
 * The shared period control: a from/to pair for a span you look *across*
 * (chart, preview, timelapse extent, Lab scene search). Consistent labels so
 * the same vocabulary reads the same everywhere.
 */
import { useEffect } from "react";
import { boundsCaption, clampDate, clampPeriod, type DateBounds } from "../../lib/coverage";
import type { Period } from "../../lib/timeWindow";

interface PeriodPickerProps {
  period: Period;
  onChange(start: string, end: string): void;
  /** Section label ("Period" by default; e.g. "Search period" in the Lab). */
  label?: string;
  compact?: boolean;
  /** Archive coverage of the product(s) shown; both ends are clamped into it. */
  bounds?: DateBounds;
}

export function PeriodPicker({
  period,
  onChange,
  label = "Period",
  compact = false,
  bounds = {},
}: PeriodPickerProps) {
  const { min, max } = bounds;
  // Re-clamp when the bounds move under a stored period (product switch,
  // preset, workspace restore) — idempotent, so this settles after one write.
  useEffect(() => {
    const clamped = clampPeriod(period, { min, max });
    if (clamped !== period) onChange(clamped.start, clamped.end);
  }, [period, min, max, onChange]);
  const coverage = boundsCaption(bounds);
  return (
    <div className={compact ? "period-picker compact" : "period-picker"}>
      {label ? <span className="period-label muted">{label}</span> : null}
      <div className="date-inputs">
        <label>
          From
          <input
            type="date"
            value={period.start}
            min={min}
            max={period.end}
            title={coverage ?? undefined}
            onChange={(e) =>
              e.target.value && onChange(clampDate(e.target.value, bounds), period.end)
            }
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={period.end}
            min={period.start}
            max={max}
            title={coverage ?? undefined}
            onChange={(e) =>
              e.target.value && onChange(period.start, clampDate(e.target.value, bounds))
            }
          />
        </label>
      </div>
      {coverage && !compact ? <p className="muted period-coverage">{coverage}</p> : null}
    </div>
  );
}
