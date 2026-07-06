import { useEffect, useMemo, useState } from "react";
import { useRenderDetail, useRenders } from "../../api/timelapseQueries";
import { useMapContext } from "../../map/MapContext";
import { useBrowseFrames } from "../../map/useBrowseFrames";
import { useImageFrames, type FrameRender } from "../../map/useImageFrames";
import { dateAxis, roiEnvelope } from "../../lib/animation";
import { useDateStore } from "../../stores/dateStore";
import { useLayersStore, type Layer } from "../../stores/layersStore";

type MapInstance = ReturnType<typeof useMapContext>["map"];

type Mode = "browse" | "playback";

/**
 * Explore animation transport with two modes (see docs/plan.md's split):
 *  - Browse: a date slider that mints date_window composites into a ±2
 *    preloaded raster-source pool (visible swap only; no re-mint).
 *  - Playback: overlays a finished render's frames as a MapLibre image source,
 *    driven by the shared frame transport (all frames preloaded first).
 */
export function AnimationBar() {
  const { map, ready } = useMapContext();
  const layers = useLayersStore((s) => s.layers);
  const active = layers.length > 0 ? layers[layers.length - 1]! : null;

  const [mode, setMode] = useState<Mode>("browse");
  const [enabled, setEnabled] = useState(false);

  if (!active) {
    return <p className="muted small">Add a layer to animate it over time.</p>;
  }

  return (
    <div className="animation-bar">
      <div className="anim-head">
        <div className="method-toggle">
          {(["browse", "playback"] as const).map((m) => (
            <button
              key={m}
              className={mode === m ? "toggle active" : "toggle"}
              onClick={() => setMode(m)}
            >
              {m === "browse" ? "Browse" : "Playback"}
            </button>
          ))}
        </div>
        <label className="loop-row">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          On
        </label>
      </div>

      <p className="muted small">
        Animating <b>{active.label}</b>
      </p>

      {mode === "browse" ? (
        <BrowseControls map={map} ready={ready} layer={active} enabled={enabled} />
      ) : (
        <PlaybackControls map={map} ready={ready} layer={active} enabled={enabled} />
      )}
    </div>
  );
}

function BrowseControls({
  map,
  ready,
  layer,
  enabled,
}: {
  map: MapInstance;
  ready: boolean;
  layer: Layer;
  enabled: boolean;
}) {
  const start = useDateStore((s) => s.start);
  const end = useDateStore((s) => s.end);
  const halfWindowDays = useDateStore((s) => s.halfWindowDays);
  const [steps, setSteps] = useState(12);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [fps, setFps] = useState(4);

  const dates = useMemo(() => dateAxis(start, end, steps), [start, end, steps]);
  // Clamp during render (dates can shrink when "Frames" drops) instead of
  // resetting index in an effect.
  const safeIndex = Math.min(index, Math.max(0, dates.length - 1));

  useEffect(() => {
    if (!enabled || !playing || dates.length < 2) return;
    const id = setInterval(() => setIndex((i) => (i + 1) % dates.length), 1000 / fps);
    return () => clearInterval(id);
  }, [enabled, playing, dates.length, fps]);

  const { status } = useBrowseFrames(map, ready, layer, dates, safeIndex, {
    enabled,
    halfWindowDays,
    opacity: layer.opacity,
  });

  return (
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
          max={Math.max(0, dates.length - 1)}
          value={safeIndex}
          disabled={!enabled}
          onChange={(e) => {
            setPlaying(false);
            setIndex(Number(e.target.value));
          }}
        />
        <span className="player-index">{dates[safeIndex] ?? "—"}</span>
      </div>
      <label className="slider-row">
        fps <b>{fps}</b>
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
            className={`frame-dot ${status[i] ?? "pending"} ${i === safeIndex ? "current" : ""}`}
            title={dates[i]}
          />
        ))}
      </div>
    </div>
  );
}

function PlaybackControls({
  map,
  ready,
  layer,
  enabled,
}: {
  map: MapInstance;
  ready: boolean;
  layer: Layer;
  enabled: boolean;
}) {
  const { data: renders } = useRenders();
  const [renderId, setRenderId] = useState<string | null>(null);
  const [fps, setFps] = useState(6);
  const [loop, setLoop] = useState(true);

  const matching = useMemo(
    () =>
      (renders ?? []).filter(
        (r) =>
          r.status === "succeeded" && r.dataset === layer.dataset && r.product === layer.product,
      ),
    [renders, layer.dataset, layer.product],
  );

  const detail = useRenderDetail(enabled && renderId ? renderId : null);
  const frameRender: FrameRender | null =
    detail.data?.frame_count != null
      ? {
          id: detail.data.id,
          frameCount: detail.data.frame_count,
          bbox: roiEnvelope(detail.data.roi),
        }
      : null;

  const transport = useImageFrames(map, ready, frameRender, { fps, loop, enabled });

  if (matching.length === 0) {
    return (
      <p className="muted small">
        No finished render matches this layer. Create one in the Timelapse Studio.
      </p>
    );
  }

  return (
    <div className="anim-controls">
      <label>
        Render
        <select value={renderId ?? ""} onChange={(e) => setRenderId(e.target.value || null)}>
          <option value="">Select a render…</option>
          {matching.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title}
            </option>
          ))}
        </select>
      </label>
      <div className="player-controls">
        <button className="mini" disabled={!enabled || !transport.ready} onClick={transport.toggle}>
          {transport.playing ? "⏸" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, transport.total - 1)}
          value={transport.index}
          disabled={!transport.ready}
          onChange={(e) => transport.seek(Number(e.target.value))}
        />
        <span className="player-index">
          {transport.total ? `${transport.index + 1}/${transport.total}` : "—"}
        </span>
      </div>
      {renderId && !transport.ready ? (
        <p className="muted small">
          Loading frames… {transport.loadedCount}/{transport.total}
        </p>
      ) : null}
      <label className="slider-row">
        fps <b>{fps}</b>
        <input
          type="range"
          min={1}
          max={30}
          value={fps}
          onChange={(e) => setFps(Number(e.target.value))}
        />
      </label>
      <label className="loop-row">
        <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
        Loop
      </label>
    </div>
  );
}
