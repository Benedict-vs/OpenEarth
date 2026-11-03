/** Small reusable inspector controls for the Cut Studio. */

export interface SegOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

/** A segmented single-select (the Studio's compact enum control). */
export function Seg<T extends string>({
  value,
  options,
  onChange,
  disabled,
  ariaLabel,
}: {
  value: T;
  options: SegOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <div className="cut-seg" role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? "sel" : ""}
          aria-pressed={o.value === value}
          disabled={disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A labelled slider with a live monospace value read-out. */
export function Slider({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`cut-slider ${disabled ? "disabled" : ""}`}>
      <span className="cut-slider-label">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="cut-slider-val mono">{display}</span>
    </label>
  );
}
