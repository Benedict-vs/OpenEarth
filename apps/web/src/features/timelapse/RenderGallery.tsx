import { useState } from "react";
import {
  downloadUrl,
  frameUrl,
  stillUrl,
  useDeleteRender,
  useRenameRender,
  useRenders,
} from "../../api/timelapseQueries";
import type { Render } from "../../api/types";
import { usePlaybackStore } from "../../stores/playbackStore";
import { useUiStore } from "../../stores/uiStore";

/** A render with playable frames: a full success, or a salvaged partial. */
function isPlayable(r: Render): boolean {
  return (r.status === "succeeded" || r.status === "cancelled") && !!r.frame_count;
}

/** Middle rendered frame index — a representative mid-sequence still. */
function midFrame(r: Render): number {
  return Math.floor((r.frame_count ?? 1) / 2);
}

export function RenderGallery({
  activeId,
  onSelect,
  onRenderFinal,
}: {
  activeId: string | null;
  onSelect: (render: Render) => void;
  onRenderFinal: (renderId: string) => void;
}) {
  const { data: renders } = useRenders();
  const del = useDeleteRender();
  const rename = useRenameRender();
  const setPlaybackRender = usePlaybackStore((s) => s.setRenderId);
  const navigate = useUiStore((s) => s.navigate);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const promptRename = (r: Render) => {
    const title = window.prompt("Rename render", r.title)?.trim();
    if (title && title !== r.title) rename.mutate({ id: r.id, title });
  };

  /** Play a finished render on the Explore map (the relocated Playback mode). */
  const playOnMap = (r: Render) => {
    setPlaybackRender(r.id);
    navigate("explore");
  };

  if (!renders || renders.length === 0) {
    return <p className="muted">No renders yet — configure a timelapse and press Render.</p>;
  }

  return (
    <ul className="render-gallery">
      {renders.map((r) => {
        const playable = isPlayable(r);
        const poster = playable ? frameUrl(r.id, midFrame(r)) : null;
        const statusLabel = r.status === "cancelled" && r.frame_count ? "partial" : r.status;
        return (
          <li key={r.id} className={activeId === r.id ? "render-card active" : "render-card"}>
            <button
              type="button"
              className="render-poster"
              disabled={!playable}
              aria-label={`Open ${r.title}`}
              onClick={() => playable && onSelect(r)}
            >
              {poster ? (
                <img src={poster} alt="" width={320} height={200} loading="lazy" />
              ) : (
                <span className={`render-placeholder ${r.status}`}>
                  {r.status === "running" ? "rendering…" : r.status}
                </span>
              )}
              <span className={`status-chip ${r.status}`}>{statusLabel}</span>
              {r.draft ? <span className="draft-chip">Draft</span> : null}
            </button>

            <div className="render-meta">
              <span className="render-title" title={r.title}>
                {r.title}
              </span>
              <span className="render-sub">
                {r.status === "cancelled" && r.frame_count
                  ? `Partial · ${r.frame_count} frames`
                  : `${r.format.toUpperCase()}${r.frame_count != null ? ` · ${r.frame_count} frames` : ""}`}
              </span>
            </div>

            <div className="render-actions">
              {r.draft && playable ? (
                <button
                  className="mini primary"
                  title="Re-render at full settings"
                  onClick={() => onRenderFinal(r.id)}
                >
                  Render final
                </button>
              ) : null}
              {playable ? (
                <button
                  className="mini"
                  title="Play this render's frames on the Explore map"
                  onClick={() => playOnMap(r)}
                >
                  ▶ Map
                </button>
              ) : null}
              {playable ? (
                <a className="mini" href={downloadUrl(r.id)} download title="Download the movie">
                  Movie
                </a>
              ) : null}
              {r.crops?.map((crop) => (
                <a
                  key={crop}
                  className="mini"
                  href={downloadUrl(r.id, crop)}
                  download
                  title={`Download the ${crop} crop`}
                >
                  {crop}
                </a>
              ))}
              {playable ? (
                <a
                  className="mini"
                  href={stillUrl(r.id, midFrame(r))}
                  download
                  title="Download a full-res still"
                >
                  Still
                </a>
              ) : null}
              <button
                className="mini"
                aria-label={`Rename ${r.title}`}
                title="Rename"
                onClick={() => promptRename(r)}
              >
                ✎
              </button>
              {confirmId === r.id ? (
                <button
                  className="mini danger"
                  onClick={() => {
                    del.mutate(r.id);
                    setConfirmId(null);
                  }}
                >
                  Confirm?
                </button>
              ) : (
                <button
                  className="mini"
                  aria-label={`Delete ${r.title}`}
                  disabled={r.status === "running"}
                  title={r.status === "running" ? "Cannot delete while rendering" : "Delete"}
                  onClick={() => setConfirmId(r.id)}
                >
                  ✕
                </button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
