import { useState } from "react";
import { useDetections } from "../../api/methaneQueries";
import type { Detection } from "../../api/types";
import { formatEmission } from "../../lib/methane";
import { useMethaneStore } from "../../stores/methaneStore";

type SourceFilter = "all" | "physics" | "ml";

export function DetectionFeed() {
  const site = useMethaneStore((s) => s.selectedSite);
  const selectedId = useMethaneStore((s) => s.selectedDetectionId);
  const select = useMethaneStore((s) => s.selectDetection);
  const [filter, setFilter] = useState<SourceFilter>("all");
  const { data: detections } = useDetections(site?.id ?? null, filter === "all" ? null : filter);

  if (!site) return <p className="muted">Select a site to see its detections.</p>;

  return (
    <>
      <div className="source-filter">
        {(["all", "physics", "ml"] as const).map((f) => (
          <button
            key={f}
            className={filter === f ? "toggle active" : "toggle"}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : f === "ml" ? "ML" : "Physics"}
          </button>
        ))}
      </div>
      {!detections || detections.length === 0 ? (
        <p className="muted">No detections yet.</p>
      ) : (
        <ul className="detection-feed">
          {detections.map((d) => (
            <DetectionCard
              key={d.id}
              detection={d}
              active={selectedId === d.id}
              onClick={() => select(d.id)}
            />
          ))}
        </ul>
      )}
    </>
  );
}

function DetectionCard({
  detection,
  active,
  onClick,
}: {
  detection: Detection;
  active: boolean;
  onClick: () => void;
}) {
  const noPlume = detection.flags.includes("no_plume");
  const isMl = detection.source === "ml";
  return (
    <li className={active ? "detection-card active" : "detection-card"} onClick={onClick}>
      <div className="card-top">
        <span className="card-date">{detection.scene_time_utc.slice(0, 10)}</span>
        <span className={`status-chip ${detection.status}`}>{detection.status}</span>
      </div>
      <div className="card-q">
        {noPlume ? (
          <span className="muted">no plume ≥ kσ</span>
        ) : (
          formatEmission(detection.q_kg_h, detection.q_sigma_kg_h)
        )}
      </div>
      <div className="card-meta">
        <span className={`source-badge ${detection.source}`}>{detection.source}</span>
        <span className="method-tag">{detection.method.toUpperCase()}</span>
        {isMl && detection.score != null ? (
          <span className="score-tag" title="Max candidate probability">
            score {detection.score.toFixed(2)}
          </span>
        ) : null}
        {detection.emit_matches != null && detection.emit_matches > 0 ? (
          <span className="emit-tag" title="Coincident EMIT plume(s)">
            EMIT ×{detection.emit_matches}
          </span>
        ) : null}
        {detection.flags
          .filter((f) => f !== "no_plume")
          .map((f) => (
            <span key={f} className="flag-tag" title="QC flag">
              {f}
            </span>
          ))}
      </div>
    </li>
  );
}
