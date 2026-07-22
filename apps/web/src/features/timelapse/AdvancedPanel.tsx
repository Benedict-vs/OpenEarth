import type { CropRatio } from "../../stores/timelapseStore";
import { useTimelapseStore } from "../../stores/timelapseStore";
import { Seg, Slider } from "./StudioControls";

const MAX_DIM_CAP = 3840;
const CROPS: CropRatio[] = ["1:1", "9:16"];

/**
 * The "every knob" panel — a preset moves these, it never hides them. Display-only
 * knobs (cloud fill/tint, deflicker) are disabled for scientific products: the API
 * refuses post-processing on non-RGB data (the honesty wall), so the UI refuses it
 * first rather than surfacing a 422.
 */
export function AdvancedPanel({
  productIsRgb,
  nativeMaxDim,
}: {
  productIsRgb: boolean;
  nativeMaxDim: number | null;
}) {
  const form = useTimelapseStore((s) => s.form);
  const setForm = useTimelapseStore((s) => s.setForm);

  const dimMax = Math.min(MAX_DIM_CAP, nativeMaxDim ?? MAX_DIM_CAP);
  const effectiveDim = Math.min(form.maxDim, dimMax);
  const toggleCrop = (crop: CropRatio) =>
    setForm({ crops: form.crops.includes(crop) ? form.crops.filter((c) => c !== crop) : [...form.crops, crop] });

  return (
    <details className="cut-adv">
      <summary>Advanced — every knob</summary>
      <div className="cut-adv-body">
        {!productIsRgb ? (
          <p className="cut-note muted">
            Scientific product — grade, gap-fill, and deflicker are off (they would alter data
            values). Composite mode and the vis range still apply.
          </p>
        ) : null}

        <label className="cut-fld">
          <span>Composite</span>
          <Seg
            ariaLabel="Composite mode"
            value={form.composite}
            onChange={(composite) => setForm({ composite })}
            options={[
              { value: "mean", label: "Mean", title: "Average all scenes in the window" },
              { value: "median", label: "Median", title: "Per-pixel median — rejects cloud outliers" },
              { value: "clearest", label: "Clearest", title: "The least-cloudy observation per pixel" },
            ]}
          />
        </label>

        <label className="cut-fld">
          <span>Cloud gaps</span>
          <Seg
            ariaLabel="Cloud gap handling"
            value={form.cloudMode}
            disabled={!productIsRgb}
            onChange={(cloudMode) => setForm({ cloudMode })}
            options={[
              { value: "fill", label: "Fill", title: "Forward-fill from a clear day within 2 windows" },
              { value: "tint", label: "Tint", title: "Flag remaining holes with a colour (Survey)" },
              { value: "show", label: "Show", title: "Leave holes transparent" },
            ]}
          />
        </label>
        {form.cloudMode === "tint" && productIsRgb ? (
          <label className="cut-fld inline">
            <span>Gap tint</span>
            <input
              type="color"
              value={form.tintColor}
              onChange={(e) => setForm({ tintColor: e.target.value })}
              aria-label="Gap flag colour"
            />
          </label>
        ) : null}

        <label className="cut-fld">
          <span>Deflicker</span>
          <Seg
            ariaLabel="Deflicker"
            value={form.deflicker ? "on" : "off"}
            disabled={!productIsRgb}
            onChange={(v) => setForm({ deflicker: v === "on" })}
            options={[
              { value: "on", label: "On", title: "Even out exposure flicker (±20% anchor)" },
              { value: "off", label: "Off" },
            ]}
          />
        </label>

        <label className="cut-fld">
          <span>Fallback source</span>
          <Seg
            ariaLabel="Fallback source"
            value={form.fallback ? "on" : "off"}
            onChange={(v) => setForm({ fallback: v === "on" })}
            options={[
              { value: "on", label: "HLS 30 m", title: "Step down to HLS when the window is empty" },
              { value: "off", label: "Off" },
            ]}
          />
        </label>

        <Slider
          label="Resolution"
          value={effectiveDim}
          min={240}
          max={dimMax}
          step={40}
          display={`${effectiveDim} px${nativeMaxDim ? " · native lock" : ""}`}
          onChange={(maxDim) => setForm({ maxDim })}
        />
        {nativeMaxDim ? (
          <p className="cut-note muted">
            Native limit for this region: {nativeMaxDim} px — frames are never enlarged past what the
            sensor measured.
          </p>
        ) : null}

        <div className="cut-fld-row">
          <label className="cut-num">
            Vis min
            <input
              type="number"
              placeholder="auto"
              value={form.visMin ?? ""}
              onChange={(e) => setForm({ visMin: e.target.value ? Number(e.target.value) : null })}
            />
          </label>
          <label className="cut-num">
            Vis max
            <input
              type="number"
              placeholder="auto"
              value={form.visMax ?? ""}
              onChange={(e) => setForm({ visMax: e.target.value ? Number(e.target.value) : null })}
            />
          </label>
        </div>

        <fieldset className="cut-annot">
          <legend>Annotations</legend>
          <label>
            <input type="checkbox" checked={form.dateLabel} onChange={(e) => setForm({ dateLabel: e.target.checked })} />
            Date label
          </label>
          <label>
            <input type="checkbox" checked={form.colorbar} onChange={(e) => setForm({ colorbar: e.target.checked })} />
            Colorbar
          </label>
          <label>
            <input type="checkbox" checked={form.scaleBar} onChange={(e) => setForm({ scaleBar: e.target.checked })} />
            Scale bar
          </label>
        </fieldset>

        <fieldset className="cut-extras">
          <legend>Share extras</legend>
          <label className="cut-text">
            Title card
            <input
              type="text"
              maxLength={120}
              placeholder="Optional opening card"
              value={form.titleCard}
              onChange={(e) => setForm({ titleCard: e.target.value })}
            />
          </label>
          <label className="cut-text">
            End card
            <input
              type="text"
              maxLength={120}
              placeholder="Optional closing card"
              value={form.endCard}
              onChange={(e) => setForm({ endCard: e.target.value })}
            />
          </label>
          <label className="cut-text">
            Watermark
            <input
              type="text"
              maxLength={60}
              placeholder="Corner caption"
              value={form.watermark}
              onChange={(e) => setForm({ watermark: e.target.value })}
            />
          </label>
          <div className="cut-crops">
            <span>Crops</span>
            {CROPS.map((crop) => (
              <button
                key={crop}
                type="button"
                className={form.crops.includes(crop) ? "chip on" : "chip"}
                aria-pressed={form.crops.includes(crop)}
                onClick={() => toggleCrop(crop)}
              >
                {crop}
              </button>
            ))}
          </div>
        </fieldset>
      </div>
    </details>
  );
}
