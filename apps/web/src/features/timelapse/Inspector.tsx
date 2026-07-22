import type { Aoi, Dataset, Product, RoiIn, RoiPreset } from "../../api/types";
import { activePreset, PRESETS, presetModifiesPixels } from "../../lib/presets";
import type { PacingSummary } from "../../lib/timelapse";
import { useTimelapseStore } from "../../stores/timelapseStore";
import { PeriodPicker } from "../explore/PeriodPicker";
import { AdvancedPanel } from "./AdvancedPanel";
import { Seg, Slider } from "./StudioControls";

interface Props {
  datasets: Dataset[] | undefined;
  products: Product[];
  productKey: string;
  productIsRgb: boolean;
  aois: Aoi[] | undefined;
  roiPresets: RoiPreset[] | undefined;
  roi: RoiIn | null;
  nativeMaxDim: number | null;
  pacing: PacingSummary | null;
  running: boolean;
  onRender: (draft: boolean) => void;
  submitError: string | null;
}

/** The Cut inspector: the authoring stack, top-to-bottom, with a sticky run row. */
export function Inspector(props: Props) {
  const { datasets, products, productKey, productIsRgb, aois, roiPresets, roi, pacing, running } = props;
  const form = useTimelapseStore((s) => s.form);
  const setForm = useTimelapseStore((s) => s.setForm);
  const active = activePreset(form);
  const canRender = !!roi && !!productKey && !running;

  return (
    <aside className="cut-inspector">
      <div className="cut-insec-scroll">
        {/* ── Source ── */}
        <Section title="Source" hint="area">
          <label className="cut-field">
            <span>Region</span>
            <select value={form.roiSource} onChange={(e) => setForm({ roiSource: e.target.value })}>
              <option value="current">Current map ROI</option>
              {aois?.map((a) => (
                <option key={a.id} value={`aoi:${a.id}`}>
                  AOI · {a.name}
                </option>
              ))}
              {roiPresets?.map((p) => (
                <option key={p.name} value={`preset:${p.name}`}>
                  Preset · {p.name}
                </option>
              ))}
            </select>
          </label>
          {!roi ? <p className="cut-note warn">No region — draw an ROI in Explore or pick one above.</p> : null}
          <div className="cut-field-row">
            <label className="cut-field">
              <span>Dataset</span>
              <select
                value={form.datasetId}
                onChange={(e) => setForm({ datasetId: e.target.value, productKey: "" })}
              >
                {datasets?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="cut-field">
              <span>Product</span>
              <select value={productKey} onChange={(e) => setForm({ productKey: e.target.value })}>
                {products.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </Section>

        {/* ── Span ── */}
        <Section title="Span" hint="when">
          <PeriodPicker
            period={{ start: form.start, end: form.end }}
            onChange={(start, end) => setForm({ start, end })}
            label=""
          />
          <label className="cut-field">
            <span>Cadence</span>
            <Seg
              ariaLabel="Cadence"
              value={form.stepMode}
              onChange={(stepMode) => setForm({ stepMode })}
              options={[
                { value: "monthly", label: "Monthly" },
                { value: "quarterly", label: "Quarterly" },
                { value: "interval", label: "Interval" },
              ]}
            />
          </label>
          {form.stepMode === "interval" ? (
            <div className="cut-field-row">
              <label className="cut-num" title="How far apart consecutive frames start">
                Every (days)
                <input
                  type="number"
                  min={1}
                  value={form.intervalDays}
                  onChange={(e) => setForm({ intervalDays: Number(e.target.value) })}
                />
              </label>
              <label className="cut-num" title="Days of scenes averaged into each frame (defaults to the interval)">
                Window (days)
                <input
                  type="number"
                  min={1}
                  placeholder="= interval"
                  value={form.windowDays ?? ""}
                  onChange={(e) => setForm({ windowDays: e.target.value ? Number(e.target.value) : null })}
                />
              </label>
            </div>
          ) : null}
        </Section>

        {/* ── Look (presets) ── */}
        <Section title="Look" hint="preset">
          <div className="cut-presets" role="group" aria-label="Presets">
            {PRESETS.map((p) => {
              const blocked = !productIsRgb && presetModifiesPixels(p);
              return (
                <button
                  key={p.id}
                  type="button"
                  className="cut-preset"
                  aria-pressed={active?.id === p.id}
                  disabled={blocked}
                  title={blocked ? "Needs an RGB product — this look grades or fills pixels" : p.tagline}
                  onClick={() => setForm(p.patch)}
                >
                  <span className="cut-preset-head">
                    <span className="cut-preset-glyph" aria-hidden>
                      {p.glyph}
                    </span>
                    <strong>{p.name}</strong>
                  </span>
                  <span className="cut-preset-tag">{p.tagline}</span>
                  <span className="cut-preset-policy">{p.policy}</span>
                  <span className="cut-preset-chips">
                    {p.chips.map((c) => (
                      <span key={c} className="chip on">
                        {c}
                      </span>
                    ))}
                  </span>
                </button>
              );
            })}
          </div>
          <p className="cut-note muted">
            {active ? `${active.name} — every value below is still editable.` : "Custom recipe."}
          </p>
        </Section>

        {/* ── Grade ── */}
        <Section title="Grade" hint={productIsRgb ? "colour" : "display only"}>
          <Seg
            ariaLabel="Grade curve"
            value={form.gradeCurve}
            disabled={!productIsRgb}
            onChange={(gradeCurve) => setForm({ gradeCurve })}
            options={[
              { value: "natural", label: "Natural" },
              { value: "vivid", label: "Vivid" },
              { value: "cinematic", label: "Cinematic" },
            ]}
          />
          <Slider
            label="Brightness"
            value={form.gradeBrightness}
            min={-0.5}
            max={0.5}
            step={0.01}
            display={signed(form.gradeBrightness)}
            disabled={!productIsRgb}
            onChange={(gradeBrightness) => setForm({ gradeBrightness })}
          />
          <Slider
            label="Contrast"
            value={form.gradeContrast}
            min={-0.5}
            max={0.5}
            step={0.01}
            display={signed(form.gradeContrast)}
            disabled={!productIsRgb}
            onChange={(gradeContrast) => setForm({ gradeContrast })}
          />
          <Slider
            label="Saturation"
            value={form.gradeSaturation}
            min={0}
            max={2}
            step={0.01}
            display={form.gradeSaturation.toFixed(2)}
            disabled={!productIsRgb}
            onChange={(gradeSaturation) => setForm({ gradeSaturation })}
          />
        </Section>

        {/* ── Motion ── */}
        <Section title="Motion" hint="pacing">
          <Seg
            ariaLabel="Pacing mode"
            value={form.authoringMode}
            onChange={(authoringMode) => setForm({ authoringMode })}
            options={[
              { value: "fps", label: "Frame rate", title: "Pick the frames per second directly" },
              { value: "duration", label: "Duration", title: "Pick a length; the fps is derived" },
            ]}
          />
          {form.authoringMode === "fps" ? (
            <Slider
              label="Speed"
              value={form.fps}
              min={1}
              max={30}
              step={1}
              display={`${form.fps} fps`}
              onChange={(fps) => setForm({ fps })}
            />
          ) : (
            <Slider
              label="Length"
              value={form.durationS}
              min={2}
              max={60}
              step={1}
              display={`${form.durationS} s`}
              onChange={(durationS) => setForm({ durationS })}
            />
          )}
          {pacing ? <p className="cut-pacing mono">{pacing.label}</p> : null}
          <div className="cut-field-row">
            <label className="cut-field">
              <span>Smoothing</span>
              <Seg
                ariaLabel="Smoothing"
                value={String(form.tween)}
                onChange={(v) => setForm({ tween: Number(v) })}
                options={[
                  { value: "0", label: "Off" },
                  { value: "1", label: "2×" },
                  { value: "3", label: "4×" },
                ]}
              />
            </label>
            <label className="cut-field">
              <span>Format</span>
              <Seg
                ariaLabel="Format"
                value={form.format}
                onChange={(format) => setForm({ format })}
                options={[
                  { value: "mp4", label: "MP4" },
                  { value: "webm", label: "WebM" },
                  { value: "gif", label: "GIF" },
                ]}
              />
            </label>
          </div>
        </Section>

        <AdvancedPanel productIsRgb={productIsRgb} nativeMaxDim={props.nativeMaxDim} />

        {/* ── Frame data (honesty recap of the current recipe) ── */}
        <Section title="Frame data" hint="honesty">
          <FrameDataRow k="Composite" v={form.composite} highlight />
          <FrameDataRow k="Cloud gaps" v={productIsRgb ? cloudLabel(form.cloudMode) : "Gaps shown"} />
          <FrameDataRow k="Deflicker" v={productIsRgb && form.deflicker ? "Anchored ±20%" : "Off"} />
          <FrameDataRow k="Fallback" v={form.fallback ? "HLS 30 m on empty" : "Off"} highlight={form.fallback} />
          <FrameDataRow
            k="Resolution"
            v={props.nativeMaxDim ? `${Math.min(form.maxDim, props.nativeMaxDim)} px · native` : `${form.maxDim} px`}
          />
        </Section>
      </div>

      <div className="cut-runrow">
        {props.submitError ? (
          <p className="error-text cut-submit-err" role="alert">
            {props.submitError}
          </p>
        ) : null}
        <div className="cut-runrow-btns">
          <button
            type="button"
            className="cut-draft"
            disabled={!canRender}
            title="Quick 480p proof — same pipeline, smaller"
            onClick={() => props.onRender(true)}
          >
            Draft
          </button>
          <button
            type="button"
            className="cut-render"
            disabled={!canRender}
            onClick={() => props.onRender(false)}
          >
            {running ? "Rendering…" : "Render"}
          </button>
        </div>
      </div>
    </aside>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="cut-insec">
      <div className="cut-insec-head">
        <span>{title}</span>
        {hint ? <span className="mono">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

function FrameDataRow({ k, v, highlight }: { k: string; v: string; highlight?: boolean }) {
  return (
    <div className="cut-kv">
      <span>{k}</span>
      <span className={highlight ? "hl" : ""}>{v}</span>
    </div>
  );
}

function signed(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

function cloudLabel(mode: string): string {
  if (mode === "fill") return "Forward-fill ≤ 2 windows";
  if (mode === "tint") return "Gaps flagged";
  return "Gaps shown";
}
