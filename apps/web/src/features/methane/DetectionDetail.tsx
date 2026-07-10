import { useState } from "react";
import {
  useDetectionDetail,
  useEmitMatch,
  usePatchDetection,
  useValidateDetection,
} from "../../api/methaneQueries";
import type { DetectionDetail as DetectionDetailT, MethaneHistogram } from "../../api/types";
import {
  detectionNumbers,
  disagreementBadge,
  mlDetectionNumbers,
  verdictBadge,
} from "../../lib/methane";
import { useMethaneStore } from "../../stores/methaneStore";
import { McHistogram } from "./McHistogram";
import { ValidationPanel } from "./ValidationPanel";

/** Fixed caption — the ML tier is a candidate ranker, never an autonomous detector. */
const ML_REVIEW_CAPTION = "ML candidate — requires review; not an autonomous detection.";

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
  const isMl = detail.source === "ml";
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

      {isMl ? (
        <MlCandidatePanel detail={detail} />
      ) : noPlume ? (
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

      <EmitSection detId={detId} detail={detail} />
    </div>
  );
}

/** EMIT cross-match: independent evidence from another instrument's plume product. */
function EmitSection({ detId, detail }: { detId: string; detail: DetectionDetailT }) {
  const emitMatch = useEmitMatch();
  const emit = detail.emit_json;

  return (
    <div className="emit-section">
      <div className="emit-head">
        <h4>EMIT plumes</h4>
        <button
          className="mini"
          onClick={() => emitMatch.mutate(detId)}
          disabled={emitMatch.isPending}
        >
          {emitMatch.isPending ? "Matching…" : emit ? "Re-check" : "Match EMIT"}
        </button>
      </div>
      {emitMatch.isError ? (
        <p className="muted emit-note">
          {(emitMatch.error as Error)?.message ?? "EMIT lookup failed."}
        </p>
      ) : !emit ? (
        <p className="muted emit-note">Not checked. Cross-match against EMIT plume complexes.</p>
      ) : emit.matches.length === 0 ? (
        <p className="muted emit-note">No EMIT plume within 5 km / 3 days of this scene.</p>
      ) : (
        <ul className="emit-matches">
          {emit.matches.map((m, i) => {
            const p = m.plume;
            const v002 = p.provenance === "lpdaac_v002";
            return (
              <li key={`${p.plume_id}-${i}`}>
                <span className={`emit-chip ${v002 ? "v002" : "v001"}`}>
                  {v002 ? "V002" : "V001"}
                </span>
                <span className="emit-dist">
                  {m.distance_km.toFixed(1)} km · Δt {Math.round(m.dt_hours)} h
                </span>
                {p.q_kg_h != null ? (
                  <span className="emit-q">
                    {p.q_kg_h.toFixed(0)}
                    {p.q_sigma_kg_h != null ? ` ± ${p.q_sigma_kg_h.toFixed(0)}` : ""} kg/h
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** ML-candidate body: the review caption, model provenance, and single-pass numbers. */
function MlCandidatePanel({ detail }: { detail: DetectionDetailT }) {
  const result = (detail.result ?? {}) as Record<string, unknown>;
  const modelVersion = typeof result.model_version === "string" ? result.model_version : "—";
  const disagreement = disagreementBadge(
    typeof result.disagreement === "string" ? result.disagreement : undefined,
  );

  return (
    <>
      <div className="ml-candidate-banner">
        <span className="ml-badge">ML</span>
        <span>{ML_REVIEW_CAPTION}</span>
      </div>
      <div className="ml-chip-row">
        <span className="model-chip" title="Serving model version">
          {modelVersion}
        </span>
        {detail.score != null ? (
          <span className="score-tag" title="Max candidate probability">
            score {detail.score.toFixed(2)}
          </span>
        ) : null}
        {disagreement ? (
          <span className={disagreement.className} title="Physics vs ML agreement">
            {disagreement.label}
          </span>
        ) : null}
      </div>
      <table className="numbers-table">
        <tbody>
          {mlDetectionNumbers(detail).map((row) => (
            <tr key={row.label}>
              <th>{row.label}</th>
              <td>{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
