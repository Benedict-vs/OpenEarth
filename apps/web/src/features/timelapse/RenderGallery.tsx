import { useState } from "react";
import { frameUrl, useDeleteRender, useRenameRender, useRenders } from "../../api/timelapseQueries";
import type { Render } from "../../api/types";
import { usePlaybackStore } from "../../stores/playbackStore";
import { useUiStore } from "../../stores/uiStore";

/** Poster = the middle rendered frame (a representative mid-sequence still). */
function posterUrl(r: Render): string | null {
  if (r.status !== "succeeded" || !r.frame_count) return null;
  return frameUrl(r.id, Math.floor(r.frame_count / 2));
}

export function RenderGallery({
  activeId,
  onSelect,
}: {
  activeId: string | null;
  onSelect: (render: Render) => void;
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
        const poster = posterUrl(r);
        return (
          <li
            key={r.id}
            className={activeId === r.id ? "render-card active" : "render-card"}
            onClick={() => r.status === "succeeded" && onSelect(r)}
          >
            <div className="render-poster">
              {poster ? (
                <img src={poster} alt={r.title} loading="lazy" />
              ) : (
                <div className={`render-placeholder ${r.status}`}>
                  {r.status === "running" ? "rendering…" : r.status}
                </div>
              )}
              <span className={`status-chip ${r.status}`}>{r.status}</span>
            </div>
            <div className="render-meta">
              <span className="render-title" title={r.title}>
                {r.title}
              </span>
              <span className="render-sub">
                {r.format.toUpperCase()}
                {r.frame_count != null ? ` · ${r.frame_count} frames` : ""}
              </span>
            </div>
            <div className="render-actions">
              {r.status === "succeeded" && r.frame_count ? (
                <button
                  className="mini"
                  title="Play this render's frames on the Explore map"
                  onClick={(e) => {
                    e.stopPropagation();
                    playOnMap(r);
                  }}
                >
                  ▶ Play on map
                </button>
              ) : null}
              <button
                className="mini"
                title="Rename render"
                onClick={(e) => {
                  e.stopPropagation();
                  promptRename(r);
                }}
              >
                ✎
              </button>
              {confirmId === r.id ? (
                <button
                  className="mini danger"
                  onClick={(e) => {
                    e.stopPropagation();
                    del.mutate(r.id);
                    setConfirmId(null);
                  }}
                >
                  Confirm?
                </button>
              ) : (
                <button
                  className="mini"
                  disabled={r.status === "running"}
                  title={r.status === "running" ? "Cannot delete while rendering" : "Delete render"}
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmId(r.id);
                  }}
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
