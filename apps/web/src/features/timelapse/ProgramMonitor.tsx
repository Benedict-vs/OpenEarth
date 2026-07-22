import { useCallback, useMemo, useRef, useState } from "react";
import { downloadUrl, frameUrl } from "../../api/timelapseQueries";
import type { TimelapseManifest } from "../../api/types";
import { frameQc, pct, sourceKind } from "../../lib/manifest";
import { useFrameTransport } from "./useFrameTransport";

/** Live render progress the monitor mirrors while a job streams frames. */
export interface LiveRun {
  jobId: string;
  renderId: string;
  status: "running" | "done" | "error";
  done: number;
  total: number;
  message: string | null;
  detail?: string;
}

interface Props {
  /** A finished (or partial) render to play, with its manifest for QC badges. */
  player: { renderId: string; frameCount: number; manifest: TimelapseManifest | null } | null;
  /** A running job — takes over the monitor while it streams. */
  run: LiveRun | null;
  onStopRun?: () => void;
  /** An opt-in preview still (a mean composite of the middle window). */
  preview: { url: string; caption: string } | null;
  onPreview?: () => void;
  previewing?: boolean;
  previewError?: string | null;
  canPreview?: boolean;
}

/**
 * The program monitor — Cut's hero. One screen, four states: playing a finished
 * render (with the honesty badges the manifest records), mirroring a live render,
 * showing an opt-in preview still, or an idle call-to-action. The transport hook
 * is always mounted (empty frame list when idle) so the hooks order is stable.
 */
export function ProgramMonitor({
  player,
  run,
  onStopRun,
  preview,
  onPreview,
  previewing,
  previewError,
  canPreview,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [fps, setFps] = useState(6);
  const [loop, setLoop] = useState(true);

  const frames = useMemo(
    () =>
      player ? Array.from({ length: player.frameCount }, (_, i) => frameUrl(player.renderId, i)) : [],
    [player],
  );

  const draw = useCallback((_index: number, img: HTMLImageElement) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (canvas.width !== img.naturalWidth) canvas.width = img.naturalWidth;
    if (canvas.height !== img.naturalHeight) canvas.height = img.naturalHeight;
    canvas.getContext("2d")?.drawImage(img, 0, 0);
  }, []);

  const t = useFrameTransport(frames, { fps, loop, onFrame: draw });

  // ── Live render ── The Phase-10 pipeline writes frame PNGs only after its
  // post-processing pass (deflicker is a second pass), so individual frames
  // aren't on disk mid-render — the monitor shows honest progress, not a
  // half-written frame. The finished player (below) loads them once complete.
  if (run && run.status !== "done") {
    const pctDone = run.total ? Math.round((run.done / run.total) * 100) : 0;
    const errored = run.status === "error";
    return (
      <div className="cut-monitor">
        <div className="cut-screen live">
          <div className="cut-idle" role="status" aria-live="polite">
            {errored ? (
              <>
                <p className="cut-idle-title">Render failed</p>
                <p className="error-text">{run.detail ?? "Earth Engine error"}</p>
              </>
            ) : (
              <>
                <p className="cut-idle-title">
                  <span className="rec-dot" aria-hidden /> Rendering
                </p>
                <p className="cut-idle-hint mono">
                  frame {run.done}/{run.total || "…"} · {run.message ?? "working"}
                </p>
              </>
            )}
          </div>
          <div className="cut-badges">
            <span className="cut-badge">
              REC <b>{run.done}</b>/{run.total || "…"}
            </span>
          </div>
          <div className="cut-transport">
            <div className="cut-scrub">
              <i style={{ width: `${errored ? 100 : pctDone}%` }} />
            </div>
            <span className="cut-tc mono">{errored ? "error" : `${pctDone}%`}</span>
            {run.status === "running" && onStopRun ? (
              <button className="mini" title="Stop render — completed frames are kept" onClick={onStopRun}>
                Stop
              </button>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  // ── Finished render: play it, badge the frame the manifest describes ──
  if (player) {
    const qc = frameQc(player.manifest, t.index);
    const primary = player.manifest?.dataset ?? "";
    const kind = qc ? sourceKind(qc.source, primary) : "gap";
    return (
      <div className="cut-monitor">
        <div className="cut-screen">
          <div className="cut-canvas-wrap">
            <canvas ref={canvasRef} className="cut-canvas" />
            {!t.ready ? (
              <div className="cut-screen-empty">
                Loading frames… {t.loadedCount}/{t.total}
              </div>
            ) : null}
          </div>
          {qc ? (
            <div className="cut-badges">
              <span className={`cut-badge src-${kind}`} title="Where this frame's pixels came from">
                SRC <b>{(qc.source ?? "—").toUpperCase()}</b>
              </span>
              <span className="cut-badge" title="Share of the frame that is measured satellite pixels">
                MEASURED <b>{pct(qc.valid)}</b>
              </span>
              {qc.filled != null && qc.filled > 0 ? (
                <span className="cut-badge fill" title="Share borrowed from a clear day within 2 windows">
                  BORROWED <b>{pct(qc.filled)}</b>
                </span>
              ) : null}
            </div>
          ) : null}
          <div className="cut-transport">
            <button className="cut-play" onClick={t.toggle} disabled={!t.ready} aria-label={t.playing ? "Pause" : "Play"}>
              {t.playing ? "❚❚" : "▶"}
            </button>
            <input
              className="cut-scrub-range"
              type="range"
              min={0}
              max={Math.max(0, player.frameCount - 1)}
              value={t.index}
              onChange={(e) => t.seek(Number(e.target.value))}
              disabled={!t.ready}
              aria-label="Scrub frames"
            />
            <span className="cut-tc mono">
              {t.index + 1}/{player.frameCount}
              {qc ? ` · ${qc.label}` : ""}
            </span>
          </div>
        </div>
        <div className="cut-monitor-opts">
          <label className="cut-opt">
            fps <b className="mono">{fps}</b>
            <input type="range" min={1} max={30} value={fps} onChange={(e) => setFps(Number(e.target.value))} />
          </label>
          <label className="cut-opt check">
            <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> Loop
          </label>
          <a className="mini" href={downloadUrl(player.renderId)} download title="Download the encoded movie">
            ↓ Movie
          </a>
        </div>
      </div>
    );
  }

  // ── Preview still ──
  if (preview) {
    return (
      <div className="cut-monitor">
        <div className="cut-screen">
          <img className="cut-screen-img" src={preview.url} alt="preview still" />
          <div className="cut-badges">
            <span className="cut-badge">PREVIEW</span>
          </div>
          <div className="cut-transport">
            <span className="cut-tc mono">{preview.caption}</span>
            {onPreview ? (
              <button className="mini" onClick={onPreview} disabled={previewing}>
                {previewing ? "Refreshing…" : "Refresh"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  // ── Idle ──
  return (
    <div className="cut-monitor">
      <div className="cut-screen idle">
        <div className="cut-idle">
          <p className="cut-idle-title">Program monitor</p>
          <p className="cut-idle-hint">
            Pick a region and a look on the right, then preview a frame or render the clip.
          </p>
          {onPreview ? (
            <button className="btn" onClick={onPreview} disabled={!canPreview || previewing}>
              {previewing ? "Minting preview…" : "Preview middle frame"}
            </button>
          ) : null}
          {!canPreview ? (
            <p className="cut-idle-note muted">Pick a region — availability loads, then a preview can be minted.</p>
          ) : null}
          {previewError ? <p className="error-text">{previewError}</p> : null}
        </div>
      </div>
    </div>
  );
}
