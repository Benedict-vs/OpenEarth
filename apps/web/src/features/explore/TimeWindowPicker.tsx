/**
 * The shared window control: a center date, width presets, a custom ± field,
 * and the pinned caption. Reusable — the Explore sidebar wires it to the shared
 * dateStore; Compare uses the `compact` variant per side against its own store.
 */
import {
  clampHalfDays,
  formatWindowCaption,
  WINDOW_HALF_MAX,
  WINDOW_HALF_MIN,
  WINDOW_PRESETS,
  type TimeWindow,
} from "../../lib/timeWindow";

interface TimeWindowPickerProps {
  window: TimeWindow;
  onChange(patch: Partial<TimeWindow>): void;
  /** Denser layout for inline / per-side use (Compare). */
  compact?: boolean;
}

export function TimeWindowPicker({ window, onChange, compact = false }: TimeWindowPickerProps) {
  const isCustom = !WINDOW_PRESETS.some((p) => p.halfDays === window.halfDays);
  return (
    <div className={compact ? "window-picker compact" : "window-picker"}>
      <label className="window-center">
        {compact ? "Center" : "Window center"}
        <input
          type="date"
          value={window.center}
          onChange={(e) => onChange({ center: e.target.value })}
        />
      </label>
      <div className="window-presets">
        {WINDOW_PRESETS.map((preset) => (
          <button
            key={preset.label}
            className={window.halfDays === preset.halfDays ? "chip active" : "chip"}
            title={preset.halfDays === 0 ? "A single day" : `± ${preset.halfDays} days`}
            onClick={() => onChange({ halfDays: preset.halfDays })}
          >
            {preset.label}
          </button>
        ))}
        <label className={isCustom ? "window-custom active" : "window-custom"}>
          ±
          <input
            type="number"
            min={WINDOW_HALF_MIN}
            max={WINDOW_HALF_MAX}
            value={window.halfDays}
            title="Custom half-width in days (0–183)"
            onChange={(e) => onChange({ halfDays: clampHalfDays(Number(e.target.value)) })}
          />
          d
        </label>
      </div>
      <p className="muted window-caption">{formatWindowCaption(window)}</p>
    </div>
  );
}
