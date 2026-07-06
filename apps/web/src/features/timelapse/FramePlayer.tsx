import { useCallback, useMemo, useRef, useState } from "react";
import { downloadUrl, frameUrl } from "../../api/timelapseQueries";
import { useFrameTransport } from "./useFrameTransport";

/** Canvas frame player: preload-gated rAF playback with scrub / fps / loop. */
export function FramePlayer({ renderId, frameCount }: { renderId: string; frameCount: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [fps, setFps] = useState(6);
  const [loop, setLoop] = useState(true);

  const frames = useMemo(
    () => Array.from({ length: frameCount }, (_, i) => frameUrl(renderId, i)),
    [renderId, frameCount],
  );

  const draw = useCallback((_index: number, img: HTMLImageElement) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (canvas.width !== img.naturalWidth) canvas.width = img.naturalWidth;
    if (canvas.height !== img.naturalHeight) canvas.height = img.naturalHeight;
    canvas.getContext("2d")?.drawImage(img, 0, 0);
  }, []);

  const t = useFrameTransport(frames, { fps, loop, onFrame: draw });

  return (
    <div className="frame-player">
      <div className="player-canvas-wrap">
        <canvas ref={canvasRef} className="player-canvas" />
        {!t.ready ? (
          <div className="player-loading">
            Loading frames… {t.loadedCount}/{t.total}
          </div>
        ) : null}
      </div>

      <div className="player-controls">
        <button className="mini" onClick={t.toggle} disabled={!t.ready}>
          {t.playing ? "⏸" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, frameCount - 1)}
          value={t.index}
          onChange={(e) => t.seek(Number(e.target.value))}
          disabled={!t.ready}
        />
        <span className="player-index">
          {t.index + 1}/{frameCount}
        </span>
      </div>

      <div className="player-options">
        <label className="fps-row">
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
        <a className="mini download-link" href={downloadUrl(renderId)} download>
          ↓ Movie
        </a>
      </div>
    </div>
  );
}
