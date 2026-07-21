import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  submitAnalyze,
  useDetectionDetail,
  useEmitMatch,
  usePatchDetection,
  useValidateDetection,
} from "../../api/methaneQueries";
import { subscribeJob } from "../../api/sse";
import type {
  AnalyzeRequest,
  DetectionDetail as DetectionDetailT,
  MethaneHistogram,
} from "../../api/types";
import {
  FLAG_HINTS,
  ML_Q_CAPTION,
  NOISE_FLOOR_TOOLTIP,
  detectionNumbers,
  disagreementBadge,
  formatFloorTh,
  mlDetectionNumbers,
  pctFraction,
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

      {detail.noise_floor_kg_h != null && detail.q_kg_h != null ? (
        <p
          className={`floor-note${detail.below_noise_floor ? " below" : ""}`}
          title={NOISE_FLOOR_TOOLTIP}
        >
          Noise floor ({detail.floor_source}): {formatFloorTh(detail.noise_floor_kg_h)} —{" "}
          {detail.below_noise_floor ? "Q is at or below it" : "Q is above it"}
        </p>
      ) : null}

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
          <FlareState detail={detail} />
          <PhysicsDiagnostics detail={detail} />
        </>
      )}

      {!isMl ? <ReferenceBlock detail={detail} /> : null}

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

/** Phase 9 flare-state callout: shown when a lit flare (NHI hot pixel) was found in
 * the target or reference. A lit→unlit transition between the two scenes can mimic a
 * plume at the stack; the hot pixels are excluded from the retrieval and the mask. */
function FlareState({ detail }: { detail: DetectionDetailT }) {
  const litTarget = detail.flags.includes("flare_lit_target");
  const litReference = detail.flags.includes("flare_lit_reference");
  if (!litTarget && !litReference) return null;

  const result = (detail.result ?? {}) as Record<string, unknown>;
  const nHotTarget = typeof result.n_hot_target === "number" ? result.n_hot_target : 0;
  const nHotReference = typeof result.n_hot_reference === "number" ? result.n_hot_reference : 0;

  const where =
    litTarget && litReference ? "target and reference" : litTarget ? "target" : "reference";
  const counts = [
    litTarget ? `${nHotTarget} px target` : null,
    litReference ? `${nHotReference} px reference` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <p className="flare-state" title="NHI hot pixels are excluded from the calibration and mask">
      🔥 Flare lit in {where} ({counts}) — a lit→unlit transition can mimic a plume at the stack;
      the hot pixels are excluded from the retrieval.
    </p>
  );
}

/** Phase 7 inversion/mask diagnostics: QC-flag hints, in-mask LUT-range clipping,
 * and the mask's k-sensitivity (fixes 2, 3, 4). */
function PhysicsDiagnostics({ detail }: { detail: DetectionDetailT }) {
  const result = (detail.result ?? {}) as Record<string, unknown>;
  const clip = (result.clip_fractions ?? {}) as Record<string, number>;
  const byK = (result.mask_npx_by_k ?? {}) as Record<string, number>;
  const flags = detail.flags.filter((f) => f !== "no_plume");
  // Sort by numeric k — JS iterates integer-like object keys ("2") before "1.5".
  const kEntries = Object.entries(byK).sort((a, b) => Number(a[0]) - Number(b[0]));
  const hasClip = Object.keys(clip).length > 0;

  if (!flags.length && !hasClip && !kEntries.length) return null;
  return (
    <div className="diagnostics">
      {flags.length > 0 ? (
        <div className="flag-hints">
          {flags.map((f) => (
            <span key={f} className="flag-hint" title={FLAG_HINTS[f] ?? "QC flag"}>
              {f}
            </span>
          ))}
        </div>
      ) : null}
      {hasClip || kEntries.length ? (
        <table className="numbers-table diag-table">
          <tbody>
            {hasClip ? (
              <tr>
                <th title="Fraction of masked pixels at the top / bottom of the reporting-LUT ΔΩ range (per pass)">
                  Inversion range hi/lo
                </th>
                <td>
                  target {pctFraction(clip.target_hi)} / {pctFraction(clip.target_lo)}
                  {detail.method === "mbmp"
                    ? ` · ref ${pctFraction(clip.ref_hi)} / ${pctFraction(clip.ref_lo)}`
                    : ""}
                </td>
              </tr>
            ) : null}
            {kEntries.length ? (
              <tr>
                <th title="Plume pixel count at each k in the Monte-Carlo threshold sweep — a large swing means an unstable mask">
                  Mask npx by k
                </th>
                <td>{kEntries.map(([k, n]) => `k${k}:${n}`).join("  ")}</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

interface ReferenceMemberView {
  scene_id: string;
  days_from_target: number;
  amf: number;
}

/**
 * The MBMP reference block: single scene id, or the composite's medianed members
 * (date, Δt, AMF). When the reference itself is contaminated, a single-mode run
 * offers "Retry with composite reference"; a composite that is *still*
 * contaminated escalates the hint to "treat this source as continuously emitting".
 */
function ReferenceBlock({ detail }: { detail: DetectionDetailT }) {
  const qc = useQueryClient();
  const selectDetection = useMethaneStore((s) => s.selectDetection);
  const setJob = useMethaneStore((s) => s.setJob);
  const [retrying, setRetrying] = useState(false);

  if (detail.method !== "mbmp" || !detail.reference_scene_id) return null;

  const result = (detail.result ?? {}) as Record<string, unknown>;
  const refMode = result.reference_mode === "composite" ? "composite" : "single";
  const members = (result.reference_members ?? []) as ReferenceMemberView[];
  const contaminated = detail.flags.includes("possible_reference_contamination");

  const retryComposite = async () => {
    const p = (detail.params ?? {}) as Record<string, unknown>;
    setRetrying(true);
    const body: AnalyzeRequest = {
      site_id: (p.site_id as number | null) ?? null,
      roi: (p.roi as AnalyzeRequest["roi"]) ?? null,
      target_scene_id: detail.scene_id,
      method: "mbmp",
      reference_mode: "composite", // escalate single → composite (drop the pinned ref)
      k_sigma: (p.k_sigma as number) ?? 2,
      min_area_px: (p.min_area_px as number) ?? 5,
      seed: (p.seed as number) ?? 0,
    };
    try {
      const { job_id } = await submitAnalyze(body);
      setJob({
        jobId: job_id,
        step: 0,
        total: 7,
        message: "Retry (composite)…",
        status: "running",
      });
      subscribeJob(job_id, {
        onProgress: (d) =>
          setJob({
            jobId: job_id,
            step: d.done,
            total: d.total,
            message: d.message,
            status: "running",
          }),
        onDone: (d) => {
          setJob({ jobId: job_id, step: 7, total: 7, message: "Done", status: "done" });
          const detId = (d.result as { detection_id?: string }).detection_id;
          if (detId) selectDetection(detId);
          void qc.invalidateQueries({ queryKey: ["methane", "detections"] });
          setRetrying(false);
        },
        onError: (d) => {
          setJob({
            jobId: job_id,
            step: 0,
            total: 7,
            message: d.detail,
            status: "error",
            detail: d.detail,
          });
          setRetrying(false);
        },
      });
    } catch {
      setRetrying(false);
    }
  };

  return (
    <div className="reference-block">
      <h4>Reference</h4>
      {refMode === "composite" ? (
        <>
          <p className="muted small">Composite — median of {members.length} same-orbit scenes.</p>
          <table className="numbers-table diag-table">
            <tbody>
              {members.map((m) => (
                <tr key={m.scene_id}>
                  <th title={m.scene_id}>{m.scene_id.slice(0, 8)}</th>
                  <td>
                    Δt {m.days_from_target >= 0 ? "+" : ""}
                    {Math.round(m.days_from_target)} d · AMF {m.amf.toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted small">Single scene: {detail.reference_scene_id}</p>
      )}
      {contaminated ? (
        <div className="contamination-hint">
          {refMode === "composite" ? (
            <p className="muted small">
              Even a {members.length}-scene composite reference shows an enhancement — treat this
              source as continuously emitting; MBSP is the honest mode here.
            </p>
          ) : (
            <>
              <p className="muted small">
                The reference itself shows an enhancement near the source — a recurrent emitter may
                have no plume-free single reference.
              </p>
              <button className="mini" disabled={retrying} onClick={retryComposite}>
                {retrying ? "Retrying…" : "Retry with composite reference"}
              </button>
            </>
          )}
        </div>
      ) : null}
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
  // Read-derived typed field (fix 8) — correct for old rows too; not result.disagreement.
  const disagreement = disagreementBadge(detail.physics_agreement);

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
      <p className="muted ml-q-note">{ML_Q_CAPTION}</p>
    </>
  );
}
