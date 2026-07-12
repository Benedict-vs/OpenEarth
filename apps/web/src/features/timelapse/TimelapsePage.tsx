import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAois, useCatalog, usePresets } from "../../api/queries";
import { subscribeJob } from "../../api/sse";
import { cancelJob, submitTimelapse, useRenderDetail } from "../../api/timelapseQueries";
import type { Render, RoiIn } from "../../api/types";
import { ApiError } from "../../api/client";
import { buildTimelapseRequest } from "../../lib/timelapse";
import { useRoiStore } from "../../stores/roiStore";
import { useTimelapseStore } from "../../stores/timelapseStore";
import { PeriodPicker } from "../explore/PeriodPicker";
import { FramePlayer } from "./FramePlayer";
import { RenderGallery } from "./RenderGallery";

interface RunState {
  jobId: string;
  renderId: string;
  status: "running" | "done" | "error";
  done: number;
  total: number;
  message: string | null;
  renderedFrames: number[];
  detail?: string;
}

/** Timelapse Studio: settings form → live render → player + gallery. */
export function TimelapsePage() {
  const qc = useQueryClient();
  const form = useTimelapseStore((s) => s.form);
  const setForm = useTimelapseStore((s) => s.setForm);
  const { data: catalog } = useCatalog();
  const { data: aois } = useAois();
  const { data: presets } = usePresets();
  const currentRoi = useRoiStore((s) => s.roi);

  const [run, setRun] = useState<RunState | null>(null);
  const [activeRenderId, setActiveRenderId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dataset = catalog?.find((d) => d.id === form.datasetId) ?? catalog?.[0] ?? null;
  const products = dataset?.products.filter((p) => !p.requires_builder) ?? [];
  const productKey = form.productKey || products[0]?.key || "";

  const roi = useMemo<RoiIn | null>(
    () => resolveRoi(form.roiSource, currentRoi, aois, presets),
    [form.roiSource, currentRoi, aois, presets],
  );

  const activeDetail = useRenderDetail(activeRenderId);
  const running = run?.status === "running";

  const submit = async () => {
    if (!roi || !productKey || !dataset) return;
    setError(null);
    const body = buildTimelapseRequest({ ...form, datasetId: dataset.id, productKey }, roi);
    try {
      const { job_id, render_id } = await submitTimelapse(body);
      setActiveRenderId(null);
      setRun({
        jobId: job_id,
        renderId: render_id,
        status: "running",
        done: 0,
        total: 0,
        message: "Queued",
        renderedFrames: [],
      });
      subscribeJob(job_id, {
        onProgress: (d) =>
          setRun((r) => (r ? { ...r, done: d.done, total: d.total, message: d.message } : r)),
        onFrame: (d) =>
          setRun((r) =>
            r && d.status === "rendered" && d.index != null
              ? { ...r, renderedFrames: [...r.renderedFrames, d.index] }
              : r,
          ),
        onDone: () => {
          setRun((r) => (r ? { ...r, status: "done" } : r));
          setActiveRenderId(render_id);
          void qc.invalidateQueries({ queryKey: ["timelapse", "renders"] });
        },
        onError: (d) => setRun((r) => (r ? { ...r, status: "error", detail: d.detail } : r)),
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  return (
    <div className="timelapse-studio">
      <aside className="studio-left">
        <h3>Timelapse Studio</h3>

        <label>
          Dataset
          <select
            value={dataset?.id ?? ""}
            onChange={(e) => setForm({ datasetId: e.target.value, productKey: "" })}
          >
            {catalog?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Product
          <select value={productKey} onChange={(e) => setForm({ productKey: e.target.value })}>
            {products.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          Region
          <select value={form.roiSource} onChange={(e) => setForm({ roiSource: e.target.value })}>
            <option value="current">Current map ROI</option>
            {aois?.map((a) => (
              <option key={a.id} value={`aoi:${a.id}`}>
                AOI · {a.name}
              </option>
            ))}
            {presets?.map((p) => (
              <option key={p.name} value={`preset:${p.name}`}>
                Preset · {p.name}
              </option>
            ))}
          </select>
        </label>
        {!roi ? (
          <p className="muted small">No region — draw an ROI in Explore or pick one.</p>
        ) : null}

        <PeriodPicker
          period={{ start: form.start, end: form.end }}
          onChange={(start, end) => setForm({ start, end })}
          label="Period"
        />

        <label>
          Step
          <select
            value={form.stepMode}
            onChange={(e) => setForm({ stepMode: e.target.value as typeof form.stepMode })}
          >
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
            <option value="interval">Fixed interval</option>
          </select>
        </label>
        {form.stepMode === "interval" ? (
          <>
            <div className="date-row">
              <label className="num-row" title="How far apart consecutive frames start">
                Every (days)
                <input
                  type="number"
                  min={1}
                  value={form.intervalDays}
                  onChange={(e) => setForm({ intervalDays: Number(e.target.value) })}
                />
              </label>
              <label
                className="num-row"
                title="How many days of scenes are averaged into each frame — defaults to the interval; larger values overlap frames and smooth cloud gaps"
              >
                Window (days)
                <input
                  type="number"
                  min={1}
                  placeholder="= interval"
                  value={form.windowDays ?? ""}
                  onChange={(e) =>
                    setForm({ windowDays: e.target.value ? Number(e.target.value) : null })
                  }
                />
              </label>
            </div>
            <p className="muted step-note">
              Each frame is one window, stepped along the period: a frame starts every{" "}
              <b>{form.intervalDays}</b> days and averages{" "}
              <b>{form.windowDays ?? form.intervalDays}</b> days of scenes. A window wider than the
              interval overlaps frames, smoothing cloud gaps.
            </p>
          </>
        ) : null}

        <div className="date-row">
          <label>
            Format
            <select
              value={form.format}
              onChange={(e) => setForm({ format: e.target.value as typeof form.format })}
            >
              <option value="mp4">MP4</option>
              <option value="webm">WebM</option>
              <option value="gif">GIF</option>
            </select>
          </label>
          <label className="num-row">
            Max dim (px)
            <input
              type="number"
              min={64}
              max={1920}
              step={40}
              value={form.maxDim}
              onChange={(e) => setForm({ maxDim: Number(e.target.value) })}
            />
          </label>
        </div>

        <label className="slider-row">
          fps <b>{form.fps}</b>
          <input
            type="range"
            min={1}
            max={30}
            value={form.fps}
            onChange={(e) => setForm({ fps: Number(e.target.value) })}
          />
        </label>

        <label>
          Smoothing
          <select value={form.tween} onChange={(e) => setForm({ tween: Number(e.target.value) })}>
            <option value={0}>Off</option>
            <option value={1}>2×</option>
            <option value={3}>4×</option>
          </select>
        </label>
        <p className="muted step-note">
          Blends between frames at encode time — a display effect, not more data.
        </p>

        <fieldset className="annotation-toggles">
          <legend>Annotations</legend>
          <label>
            <input
              type="checkbox"
              checked={form.dateLabel}
              onChange={(e) => setForm({ dateLabel: e.target.checked })}
            />
            Date label
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.colorbar}
              onChange={(e) => setForm({ colorbar: e.target.checked })}
            />
            Colorbar
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.scaleBar}
              onChange={(e) => setForm({ scaleBar: e.target.checked })}
            />
            Scale bar
          </label>
        </fieldset>

        <div className="date-row">
          <label className="num-row">
            Vis min
            <input
              type="number"
              placeholder="auto"
              value={form.visMin ?? ""}
              onChange={(e) => setForm({ visMin: e.target.value ? Number(e.target.value) : null })}
            />
          </label>
          <label className="num-row">
            Vis max
            <input
              type="number"
              placeholder="auto"
              value={form.visMax ?? ""}
              onChange={(e) => setForm({ visMax: e.target.value ? Number(e.target.value) : null })}
            />
          </label>
        </div>

        <button className="primary" disabled={!roi || !productKey || running} onClick={submit}>
          {running ? "Rendering…" : "Render timelapse"}
        </button>
        {error ? <p className="error-text">{error}</p> : null}
      </aside>

      <main className="studio-main">
        <section className="studio-stage">
          {activeRenderId && activeDetail.data?.frame_count ? (
            <FramePlayer renderId={activeRenderId} frameCount={activeDetail.data.frame_count} />
          ) : run ? (
            <LiveStrip run={run} onCancel={() => void cancelJob(run.jobId)} />
          ) : (
            <p className="muted">Configure a timelapse and press Render, or open one below.</p>
          )}
        </section>

        <section className="studio-gallery">
          <h3>Gallery</h3>
          <RenderGallery
            activeId={activeRenderId}
            onSelect={(r: Render) => setActiveRenderId(r.id)}
          />
        </section>
      </main>
    </div>
  );
}

/** Live preview strip: rendered-frame thumbs fill in as SSE frame events land. */
function LiveStrip({ run, onCancel }: { run: RunState; onCancel: () => void }) {
  const [cancelling, setCancelling] = useState(false);
  return (
    <div className="live-strip">
      <div className={`run-progress ${run.status}`}>
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${run.total ? (run.done / run.total) * 100 : 0}%` }}
          />
        </div>
        <div className="run-progress-row">
          <span className="progress-label">
            {run.status === "error"
              ? `Error: ${run.detail}`
              : `${run.done}/${run.total} · ${run.message ?? ""}`}
          </span>
          {run.status === "running" ? (
            <button
              className="mini"
              disabled={cancelling}
              title="Stop render — completed frames are kept"
              onClick={() => {
                setCancelling(true);
                onCancel();
              }}
            >
              {cancelling ? "Stopping…" : "Stop render"}
            </button>
          ) : null}
        </div>
      </div>
      <div className="frame-thumbs">
        {run.renderedFrames.map((i) => (
          <img
            key={i}
            className="frame-thumb"
            src={`/api/timelapse/${run.renderId}/frames/${i}`}
            alt={`frame ${i}`}
            loading="lazy"
          />
        ))}
      </div>
    </div>
  );
}

type Aoi = { id: number; name: string; roi: RoiIn };
type Preset = { name: string; bbox: { west: number; south: number; east: number; north: number } };

/** Resolve the ROI-source key to a concrete ROI (current map / AOI / preset). */
function resolveRoi(
  source: string,
  currentRoi: RoiIn | null,
  aois: Aoi[] | undefined,
  presets: Preset[] | undefined,
): RoiIn | null {
  if (source === "current") return currentRoi;
  if (source.startsWith("aoi:")) {
    const id = Number(source.slice(4));
    return aois?.find((a) => a.id === id)?.roi ?? null;
  }
  if (source.startsWith("preset:")) {
    const name = source.slice(7);
    const p = presets?.find((x) => x.name === name);
    return p ? { kind: "bbox", ...p.bbox } : null;
  }
  return null;
}
