/**
 * A typeable date field shared by every date control in the app.
 *
 * Commits on Enter/blur (never mid-typing), so a year can be typed outright;
 * ↑/↓ step a day, with Shift a month, with Alt/⌥ a year; Escape reverts. The
 * calendar button opens the browser's native picker where `showPicker()` exists.
 * Out-of-range values are clamped into [min, max] and the clamp is announced.
 */
import { useRef, useState } from "react";
import { type DateUnit, parseDateInput, stepDate } from "../../lib/dateInput";

interface DateFieldProps {
  value: string;
  onCommit(iso: string): void;
  min?: string | undefined;
  max?: string | undefined;
  /** Accessible name when there is no visible <label> around the field. */
  ariaLabel?: string | undefined;
  title?: string | undefined;
}

const SUPPORTS_PICKER =
  typeof HTMLInputElement !== "undefined" && "showPicker" in HTMLInputElement.prototype;

function clamp(iso: string, min?: string, max?: string): string {
  if (min && iso < min) return min;
  if (max && iso > max) return max;
  return iso;
}

export function DateField({ value, onCommit, min, max, ariaLabel, title }: DateFieldProps) {
  const [draft, setDraft] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const nativeRef = useRef<HTMLInputElement>(null);
  const invalid = draft !== null && draft.trim() !== "" && parseDateInput(draft) === null;

  const commit = (iso: string) => {
    const clamped = clamp(iso, min, max);
    setNote(
      clamped !== iso ? `Clamped to ${clamped} (allowed ${min ?? "…"} – ${max ?? "…"})` : null,
    );
    setDraft(null);
    if (clamped !== value) onCommit(clamped);
  };

  const commitDraft = () => {
    if (draft === null) return;
    const parsed = parseDateInput(draft);
    if (parsed === null) {
      if (draft.trim() === "") setDraft(null); // emptied → revert, a date is required
      return; // keep the invalid text visible (red) so it can be corrected
    }
    commit(parsed);
  };

  const step = (unit: DateUnit, delta: number) => {
    const base = (draft !== null && parseDateInput(draft)) || value;
    commit(stepDate(base, unit, delta));
  };

  return (
    <span className="date-field">
      <input
        type="text"
        inputMode="numeric"
        spellCheck={false}
        className={invalid ? "date-text invalid" : "date-text"}
        value={draft ?? value}
        placeholder="YYYY-MM-DD"
        aria-label={ariaLabel}
        aria-invalid={invalid}
        title={
          invalid
            ? "Not a date — type YYYY-MM-DD (or DD.MM.YYYY, YYYY-MM)"
            : (note ??
              `${title ? `${title} · ` : ""}Type a date; ↑/↓ = ±1 day, ⇧ = month, ⌥ = year`)
        }
        onChange={(e) => {
          setDraft(e.target.value);
          setNote(null);
        }}
        onBlur={commitDraft}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commitDraft();
          } else if (e.key === "Escape") {
            setDraft(null);
            setNote(null);
          } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            const unit: DateUnit = e.altKey ? "year" : e.shiftKey ? "month" : "day";
            step(unit, e.key === "ArrowUp" ? 1 : -1);
          }
        }}
      />
      {SUPPORTS_PICKER ? (
        <>
          <button
            type="button"
            className="date-cal-button"
            tabIndex={-1}
            aria-label="Open calendar"
            title="Open calendar"
            onClick={() => {
              try {
                nativeRef.current?.showPicker();
              } catch {
                // Some browsers refuse without a fresh user gesture — typing still works.
              }
            }}
          >
            ▦
          </button>
          {/* Rendered but invisible: showPicker() needs a laid-out <input type=date>. */}
          <input
            ref={nativeRef}
            type="date"
            className="date-native"
            tabIndex={-1}
            aria-hidden
            value={value}
            min={min}
            max={max}
            onChange={(e) => e.target.value && commit(e.target.value)}
          />
        </>
      ) : null}
      {note ? <span className="date-note">{note}</span> : null}
    </span>
  );
}
