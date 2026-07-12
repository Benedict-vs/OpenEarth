/**
 * Finished-render playback, docked on the Explore map (Phase 8: relocated here
 * from the old AnimationBar "Playback" mode). The gallery's "Play on map" sets
 * `playbackStore.renderId` and navigates to Explore; this bar reads it, overlays
 * the render's frames via the unchanged `useImageFrames` image-source transport,
 * and clears the store on ✕. Rendered inside the Explore MapProvider so it can
 * reach the map.
 */
import { useMemo, useState } from "react";
import { useRenderDetail } from "../../api/timelapseQueries";
import { roiEnvelope } from "../../lib/animation";
import { useImageFrames, type FrameRender } from "../../map/useImageFrames";
import { useMapContext } from "../../map/MapContext";
import { usePlaybackStore } from "../../stores/playbackStore";

export function PlaybackBar() {
  const { map, ready } = useMapContext();
  const renderId = usePlaybackStore((s) => s.renderId);
  const setRenderId = usePlaybackStore((s) => s.setRenderId);
  const [fps, setFps] = useState(6);
  const [loop, setLoop] = useState(true);

  const detail = useRenderDetail(renderId);
  // Memoize against the (stable) query data — a fresh object each render would
  // recompute the frame list downstream every render and loop the transport.
  const data = detail.data;
  const frameRender: FrameRender | null = useMemo(
    () =>
      renderId && data?.frame_count != null
        ? { id: data.id, frameCount: data.frame_count, bbox: roiEnvelope(data.roi) }
        : null,
    [renderId, data],
  );

  const transport = useImageFrames(map, ready, frameRender, {
    fps,
    loop,
    enabled: renderId !== null,
  });

  if (!renderId) return null;

  return (
    <div className="playback-bar">
      <div className="playback-bar-head">
        <strong title={detail.data?.title}>▶ {detail.data?.title ?? "Playing render"}</strong>
        <button className="icon" title="Stop playback" onClick={() => setRenderId(null)}>
          ×
        </button>
      </div>
      <div className="player-controls">
        <button className="mini" disabled={!transport.ready} onClick={transport.toggle}>
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
      {!transport.ready ? (
        <p className="muted small">
          Loading frames… {transport.loadedCount}/{transport.total}
        </p>
      ) : null}
      <div className="playback-bar-foot">
        <label className="slider-row">
          up to {fps} fps
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
    </div>
  );
}
