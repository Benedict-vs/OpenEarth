/**
 * The shared period control: a from/to pair for a span you look *across*
 * (chart, preview, timelapse extent, Lab scene search). Consistent labels so
 * the same vocabulary reads the same everywhere.
 */
import { useEffect } from "react";
import { boundsCaption, clampPeriod, type DateBounds } from "../../lib/coverage";
import { shiftPeriod, stepDate } from "../../lib/dateInput";
import type { Period } from "../../lib/timeWindow";
import { DateField } from "./DateField";

/** The period moves as a whole: by a year (same season) or by its own length. */
const SHIFTS = [
  { by: "year", dir: -1, label: "−1 yr", title: "Same dates, one year earlier" },
  { by: "span", dir: -1, label: "‹", title: "Previous period of the same length" },
  { by: "span", dir: 1, label: "›", title: "Next period of the same length" },
  { by: "year", dir: 1, label: "+1 yr", title: "Same dates, one year later" },
] as const;

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
          <DateField
            value={period.start}
            min={min}
            // A period needs ≥ 1 day: [start, end) ranges are empty when equal (→ 422).
            max={stepDate(period.end, "day", -1)}
            title={coverage ?? undefined}
            onCommit={(start) => onChange(start, period.end)}
          />
        </label>
        <label>
          To
          <DateField
            value={period.end}
            min={stepDate(period.start, "day", 1)}
            max={max}
            title={coverage ?? undefined}
            onCommit={(end) => onChange(period.start, end)}
          />
        </label>
      </div>
      <div className="period-shift" role="group" aria-label="Shift period">
        {SHIFTS.map((s) => {
          const next = shiftPeriod(period, s.by, s.dir);
          const outside = (min && next.start < min) || (max && next.end > max);
          return (
            <button
              key={`${s.by}${s.dir}`}
              type="button"
              className="mini ghost"
              title={outside ? `${s.title} — outside the archive (${coverage})` : s.title}
              disabled={Boolean(outside)}
              onClick={() => onChange(next.start, next.end)}
            >
              {s.label}
            </button>
          );
        })}
      </div>
      {coverage && !compact ? <p className="muted period-coverage">{coverage}</p> : null}
    </div>
  );
}
