import { useState } from "react";
import {
  useDetectionDetail,
  usePatchDetection,
  useValidateDetection,
} from "../../api/methaneQueries";
import type { MethaneHistogram } from "../../api/types";
import { detectionNumbers, verdictBadge } from "../../lib/methane";
import { useMethaneStore } from "../../stores/methaneStore";
import { McHistogram } from "./McHistogram";
import { ValidationPanel } from "./ValidationPanel";

export function DetectionDetail() {
  const detId = useMethaneStore((s) => s.selectedDetectionId);
  const { data: detail } = useDetectionDetail(detId);
  const patch = usePatchDetection();
  const validate = useValidateDetection();
  const [notes, setNotes] = useState<string>("");

  if (!detId || !detail) return <p className="muted">Run or pick a detection to see details.</p>;

  const result = (detail.result ?? {}) as Record<string, unknown>;
  const histogram = result.histogram as MethaneHistogram | undefined;
  const noPlume = detail.flags.includes("no_plume");
  const verdict = detail.validation?.verdict as string | undefined;
  const badge = verdictBadge(verdict);

  const setStatus = (status: "accepted" | "rejected" | "candidate") =>
    patch.mutate({ id: detId, body: { status } });

  return (
    <div className="detection-detail">
      <div className="detail-head">
        <div>
          <div className="detail-date">{detail.scene_time_utc.slice(0, 10)}</div>
          <div className="muted">
            {detail.method.toUpperCase()} · {detail.scene_id}
          </div>
        </div>
        <span className={`status-chip ${detail.status}`}>{detail.status}</span>
      </div>

      {noPlume ? (
        <p className="muted no-plume">No plume detected above the kσ threshold at this scene.</p>
      ) : (
        <>
          <table className="numbers-table">
            <tbody>
              {detectionNumbers(detail).map((row) => (
                <tr key={row.label}>
                  <th>{row.label}</th>
                  <td>{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="hist-block">
            <h4>Monte-Carlo Q</h4>
            <McHistogram histogram={histogram} />
          </div>
        </>
      )}

      <div className="review-row">
        <button
          className="primary"
          onClick={() => setStatus("accepted")}
          disabled={patch.isPending}
        >
          Accept
        </button>
        <button className="ghost" onClick={() => setStatus("rejected")} disabled={patch.isPending}>
          Reject
        </button>
      </div>

      <div className="notes-row">
        <textarea
          placeholder="Notes…"
          value={notes || detail.notes || ""}
          onChange={(e) => setNotes(e.target.value)}
        />
        <button
          className="mini"
          onClick={() => patch.mutate({ id: detId, body: { notes } })}
          disabled={patch.isPending}
        >
          Save note
        </button>
      </div>

      <div className="validate-row">
        <span className={badge.className}>{badge.label}</span>
        <button
          className="mini"
          onClick={() => validate.mutate(detId)}
          disabled={validate.isPending}
        >
          {validate.isPending ? "Matching…" : "Validate"}
        </button>
      </div>

      <ValidationPanel />
    </div>
  );
}
