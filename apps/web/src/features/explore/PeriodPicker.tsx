/**
 * The shared period control: a from/to pair for a span you look *across*
 * (chart, preview, timelapse extent, Lab scene search). Consistent labels so
 * the same vocabulary reads the same everywhere.
 */
import type { Period } from "../../lib/timeWindow";

interface PeriodPickerProps {
  period: Period;
  onChange(start: string, end: string): void;
  /** Section label ("Period" by default; e.g. "Search period" in the Lab). */
  label?: string;
  compact?: boolean;
}

export function PeriodPicker({ period, onChange, label = "Period", compact = false }: PeriodPickerProps) {
  return (
    <div className={compact ? "period-picker compact" : "period-picker"}>
      {label ? <span className="period-label muted">{label}</span> : null}
      <div className="date-inputs">
        <label>
          From
          <input
            type="date"
            value={period.start}
            max={period.end}
            onChange={(e) => onChange(e.target.value, period.end)}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={period.end}
            min={period.start}
            onChange={(e) => onChange(period.start, e.target.value)}
          />
        </label>
      </div>
    </div>
  );
}
