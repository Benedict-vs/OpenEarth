import { useEffect, useMemo, useState } from "react";
import { useMapContext } from "../../map/MapContext";
import { useBrowseFrames } from "../../map/useBrowseFrames";
import { advanceFrame, dateAxis, PREFETCH_MAX } from "../../lib/animation";
import { useDateStore } from "../../stores/dateStore";
import { useLayersStore, type Layer } from "../../stores/layersStore";
import { useTimelapseStore } from "../../stores/timelapseStore";
import { useUiStore } from "../../stores/uiStore";
import { PeriodPicker } from "./PeriodPicker";

const MS_PER_DAY = 24 * 3600 * 1000;

/**
 * Explore **Preview** transport (Phase 8): slides the layer window across the
 * shared period, minting `date_window`-equivalent composites on demand into a
 * hidden ±radius raster pool (visible swap only — no re-mint). Play is
 * buffer-aware: it holds on the current frame until the next is ready rather
 * than advancing over a gap. Real, smooth playback is a rendered timelapse —
 * the caption and the "Render as timelapse…" button point there.
 *
 * Finished-render playback lives in the Gallery now ("Play on map"), not here.
 */
export function AnimationBar() {
  const { map, ready } = useMapContext();
  const layers = useLayersStore((s) => s.layers);
  const active = layers.length > 0 ? layers[layers.length - 1]! : null;

  if (!active) {
    return <p className="muted small">Add a layer to preview it over time.</p>;
  }
  return <PreviewControls map={map} ready={ready} layer={active} />;
}

function PreviewControls({
  map,
  ready,
  layer,
}: {
  map: ReturnType<typeof useMapContext>["map"];
  ready: boolean;
  layer: Layer;
}) {
  const period = useDateStore((s) => s.period);
  const setPeriod = useDateStore((s) => s.setPeriod);
  const halfDays = useDateStore((s) => s.window.halfDays);
  const navigate = useUiStore((s) => s.navigate);
  const setForm = useTimelapseStore((s) => s.setForm);

  const [enabled, setEnabled] = useState(false);
  const [steps, setSteps] = useState(12);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [fps, setFps] = useState(4);
  const [prefetchAll, setPrefetchAll] = useState(false);

  const dates = useMemo(
    () => dateAxis(period.start, period.end, steps),
    [period.start, period.end, steps],
  );
  const total = dates.length;
  // Clamp during render (dates shrink when "Frames" drops) rather than in an effect.
  const safeIndex = Math.min(index, Math.max(0, total - 1));

  const { status, statusRef } = useBrowseFrames(map, ready, layer, dates, safeIndex, {
    enabled,
    halfDays,
    opacity: layer.opacity,
    // "Prefetch all" widens the pool to cover every index (still paced by
    // MAX_IN_FLIGHT); omit the key otherwise to keep the ±2 default.
    ...(prefetchAll ? { poolRadius: total } : {}),
  });

  // Buffer-aware play: the timer reads the pool's *synchronous* status ref (not
  // the one-render-behind `status` state), so it only ever advances through
  // ready frames and holds otherwise — never racing past an unready frame.
  useEffect(() => {
    if (!enabled || !playing || total < 2) return;
    const id = setInterval(
      () => setIndex((i) => advanceFrame(statusRef.current, i, total)),
      1000 / fps,
    );
    return () => clearInterval(id);
  }, [enabled, playing, total, fps, statusRef]);

  const held = playing && total > 1 && advanceFrame(status, safeIndex, total) === safeIndex;

  const renderAsTimelapse = () => {
    const periodDays = Math.max(
      1,
      Math.round(
        (new Date(`${period.end}T00:00:00Z`).getTime() -
          new Date(`${period.start}T00:00:00Z`).getTime()) /
          MS_PER_DAY,
      ),
    );
    setForm({
      title: `Preview · ${layer.label}`,
      datasetId: layer.dataset,
      productKey: layer.product,
      roiSource: "current",
      start: period.start,
      end: period.end,
      stepMode: "interval",
      intervalDays: Math.max(1, Math.ceil(periodDays / Math.max(1, total))),
      // Each frame averages the current window's width (2·halfDays + 1 days).
      windowDays: 2 * halfDays + 1 || null,
    });
    navigate("timelapse");
  };

  return (
    <div className="preview-bar">
      <div className="anim-head">
        <label className="loop-row">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Preview <b>{layer.label}</b>
        </label>
      </div>

      <PeriodPicker period={period} onChange={setPeriod} compact />

      <div className="anim-controls">
        <label className="num-row">
          Frames
          <input
            type="number"
            min={2}
            max={60}
            value={steps}
            onChange={(e) => setSteps(Math.max(2, Number(e.target.value)))}
          />
        </label>
        <div className="player-controls">
          <button className="mini" disabled={!enabled} onClick={() => setPlaying((p) => !p)}>
            {playing ? "⏸" : "▶"}
          </button>
          <input
            type="range"
            min={0}
            max={Math.max(0, total - 1)}
            value={safeIndex}
            disabled={!enabled}
            onChange={(e) => {
              setPlaying(false);
              setIndex(Number(e.target.value));
            }}
          />
          <span className="player-index">{dates[safeIndex] ?? "—"}</span>
        </div>
        {held ? <p className="muted small buffering-note">buffering…</p> : null}
        <label className="slider-row">
          up to {fps} fps
          <input
            type="range"
            min={1}
            max={12}
            value={fps}
            onChange={(e) => setFps(Number(e.target.value))}
          />
        </label>
        <div className="frame-dots">
          {dates.map((_d, i) => (
            <span
              key={i}
              className={`frame-dot ${status[i] ?? "pending"} ${i === safeIndex ? "current" : ""} ${
                i === safeIndex && held ? "buffering" : ""
              }`}
              title={dates[i]}
            />
          ))}
        </div>
        <div className="preview-actions">
          {total <= PREFETCH_MAX ? (
            <button
              className="mini"
              disabled={!enabled || prefetchAll}
              title="Preload every frame (paced by the EE budget) for smoother play"
              onClick={() => setPrefetchAll(true)}
            >
              {prefetchAll ? "Prefetching…" : "Prefetch all"}
            </button>
          ) : null}
          <button
            className="mini"
            title="Seed a Timelapse render from this preview's layer, period and window"
            onClick={renderAsTimelapse}
          >
            Render as timelapse…
          </button>
        </div>
      </div>

      <p className="muted small preview-caption">
        Slides your window across the period, minting composites on demand — for smooth playback,
        render a timelapse.
      </p>
    </div>
  );
}
