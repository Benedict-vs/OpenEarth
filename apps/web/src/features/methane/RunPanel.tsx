import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  submitAnalyze,
  submitMlScan,
  useMlStatus,
  useSiteNoiseFloor,
} from "../../api/methaneQueries";
import { subscribeJob } from "../../api/sse";
import type { AnalyzeRequest } from "../../api/types";
import {
  MAX_ANALYSIS_KM,
  MIN_ANALYSIS_KM,
  NOISE_FLOOR_TOOLTIP,
  analysisAreaPx,
  analysisAreaToBBox,
  describeApiError,
  formatFloorTh,
  isInAnalysisArea,
  isIntInRange,
  parseNumberInput,
  referenceDeltaDays,
} from "../../lib/methane";
import { isStillOnSite, useMethaneStore } from "../../stores/methaneStore";
import { CoordFields } from "./CoordFields";

/** One-line method digests (full theory in docs/methane_methods.md §1). */
const METHOD_TIPS = {
  mbmp: "MBMP: target-scene SWIR signal minus a clear reference pass — cancels static surface structure. Best default; needs a plume-free reference.",
  mbsp: "MBSP: single scene, B12 vs B11 only — no reference needed, but bright/dark terrain can mimic plumes. Reliable mainly over uniform arid surfaces.",
} as const;

export function RunPanel() {
  const qc = useQueryClient();
  const site = useMethaneStore((s) => s.selectedSite);
  const target = useMethaneStore((s) => s.targetSceneId);
  const targetTime = useMethaneStore((s) => s.targetSceneTime);
  const reference = useMethaneStore((s) => s.referenceSceneId);
  const referenceTime = useMethaneStore((s) => s.referenceSceneTime);
  const setReference = useMethaneStore((s) => s.setReference);
  const params = useMethaneStore((s) => s.params);
  const setParams = useMethaneStore((s) => s.setParams);
  const job = useMethaneStore((s) => s.job);
  const setJob = useMethaneStore((s) => s.setJob);
  const selectDetection = useMethaneStore((s) => s.selectDetection);
  const area = useMethaneStore((s) => s.analysisArea);
  const sourcePoint = useMethaneStore((s) => s.sourcePoint);
  const floor = useSiteNoiseFloor(site?.id ?? null);

  const running = job?.status === "running";
  // Blank/invalid number boxes block the run here instead of reaching the API as
  // 0 (→ 422, previously swallowed) or a negative seed (→ fails after the EE work).
  const paramsError = !isIntInRange(params.minAreaPx, 1)
    ? "Min area must be a whole number ≥ 1."
    : !isIntInRange(params.seed, 0)
      ? "Seed must be a whole number ≥ 0."
      : null;

  const run = async () => {
    if (!site || !target || !area || paramsError) return;
    const siteAtSubmit = site.id;
    const body: AnalyzeRequest = {
      site_id: site.id,
      roi: analysisAreaToBBox(area),
      target_scene_id: target,
      method: params.method,
      // Composite reference is an MBMP-only option; MBSP has no reference pass.
      reference_mode: params.method === "mbmp" ? params.referenceMode : "single",
      // The store clears the pin on composite, so this never trips the API's 422.
      reference_scene_id:
        params.method === "mbmp" && params.referenceMode === "single" && reference !== "auto"
          ? reference
          : null,
      k_sigma: params.kSigma,
      min_area_px: params.minAreaPx,
      seed: params.seed,
      // Anchors plume selection to a known emitter; unset → the strongest blob in the chip.
      source_lonlat: sourcePoint ? [sourcePoint.lon, sourcePoint.lat] : null,
    };
    let job_id: string;
    try {
      ({ job_id } = await submitAnalyze(body));
    } catch (err) {
      const detail = describeApiError(err);
      setJob({ jobId: "", step: 0, total: 7, message: detail, status: "error", detail });
      return;
    }
    setJob({ jobId: job_id, step: 0, total: 7, message: "Queued", status: "running" });
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
        if (detId && isStillOnSite(siteAtSubmit)) selectDetection(detId);
        void qc.invalidateQueries({ queryKey: ["methane", "detections"] });
      },
      onError: (d) =>
        setJob({
          jobId: job_id,
          step: 0,
          total: 7,
          message: d.detail,
          status: "error",
          detail: d.detail,
        }),
    });
  };

  return (
    <div className="run-panel">
      <div className="run-controls">
        <div className="method-toggle">
          {(["mbmp", "mbsp"] as const).map((m) => (
            <button
              key={m}
              className={params.method === m ? "toggle active" : "toggle"}
              title={METHOD_TIPS[m]}
              onClick={() => setParams({ method: m })}
            >
              {m.toUpperCase()}
            </button>
          ))}
        </div>
        <p className="muted method-note">{METHOD_TIPS[params.method]}</p>
        {params.method === "mbmp" ? (
          <fieldset className="reference-mode">
            <legend>Reference</legend>
            <label>
              <input
                type="radio"
                name="reference-mode"
                checked={params.referenceMode === "single"}
                onChange={() => setParams({ referenceMode: "single" })}
              />
              Single scene
            </label>
            <label>
              <input
                type="radio"
                name="reference-mode"
                checked={params.referenceMode === "composite"}
                onChange={() => setParams({ referenceMode: "composite" })}
              />
              Composite — median of up to 5 same-orbit scenes
            </label>
            {params.referenceMode === "composite" ? (
              <p className="muted small">
                Robust background for recurrent emitters — an intermittent plume must appear in half
                the scenes to contaminate it.
              </p>
            ) : reference === "auto" ? (
              <p className="muted small">
                Auto: the nearest clear pass, preferring the same orbit and spacecraft. To choose
                one yourself, click its ● in the Scenes table.
              </p>
            ) : (
              <p className="small ref-pinned-note">
                Pinned: <b>{referenceTime?.slice(0, 10) ?? reference}</b>
                {targetTime && referenceTime
                  ? ` (${formatDeltaDays(referenceDeltaDays(targetTime, referenceTime))} from target)`
                  : ""}{" "}
                <button className="link-button" onClick={() => setReference("auto")}>
                  use auto
                </button>
              </p>
            )}
          </fieldset>
        ) : null}
        {floor.data?.floor_kg_h != null ? (
          <p className="muted floor-context" title={NOISE_FLOOR_TOOLTIP}>
            Site noise floor: {formatFloorTh(floor.data.floor_kg_h)} ({floor.data.floor_source}
            {floor.data.detect_rate != null && floor.data.n_pairs != null
              ? ` · ${Math.round(floor.data.detect_rate * 100)}% of ${floor.data.n_pairs} plume-free pairs "detected"`
              : ""}
            )
          </p>
        ) : null}
        <label
          className="slider-row"
          title="Detection threshold in robust standard deviations of the ΔXCH4 field — higher k is stricter: fewer false positives, but weaker plumes are missed"
        >
          k·σ: <b>{params.kSigma.toFixed(2)}</b>
          <input
            type="range"
            min={1}
            max={3}
            step={0.25}
            value={params.kSigma}
            onChange={(e) => setParams({ kSigma: Number(e.target.value) })}
          />
        </label>
        <label
          className="num-row"
          title="Smallest connected group of 20 m pixels kept as a plume candidate — raises the bar against single-pixel noise"
        >
          Min area (px)
          <input
            type="number"
            min={1}
            step={1}
            value={Number.isNaN(params.minAreaPx) ? "" : params.minAreaPx}
            onChange={(e) => setParams({ minAreaPx: parseNumberInput(e.target.value) })}
          />
        </label>
        <label
          className="num-row"
          title="Random seed for the emission-rate Monte Carlo — the same seed reproduces the same Q ± σ"
        >
          Seed
          <input
            type="number"
            min={0}
            step={1}
            value={Number.isNaN(params.seed) ? "" : params.seed}
            onChange={(e) => setParams({ seed: parseNumberInput(e.target.value) })}
          />
        </label>
      </div>

      <AnalysisAreaControls />

      {paramsError ? <p className="error-text">{paramsError}</p> : null}
      <button
        className="primary run-button"
        disabled={!site || !target || !area || running || paramsError !== null}
        onClick={run}
      >
        {running ? "Running…" : "Run analysis"}
      </button>

      {job ? (
        <div className={`run-progress ${job.status}`}>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${(job.step / job.total) * 100}%` }} />
          </div>
          <span className="progress-label">
            {job.status === "error"
              ? `Error: ${job.detail}`
              : `${job.step}/${job.total} · ${job.message ?? ""}`}
          </span>
        </div>
      ) : null}

      <MlScanAction />
    </div>
  );
}

function formatDeltaDays(days: number): string {
  const rounded = Math.round(days);
  return `${rounded >= 0 ? "+" : "−"}${Math.abs(rounded)} d`;
}

/**
 * The chip-sized sub-area actually analyzed. Site ROIs are browse-scale
 * (~100 km) while the 20 m retrieval chip caps at 1024 px (~20 km), so the
 * user positions a small square within the site: sized here, recentred by
 * clicking the map ("Place on map").
 */
function AnalysisAreaControls() {
  const area = useMethaneStore((s) => s.analysisArea);
  const setArea = useMethaneStore((s) => s.setAnalysisArea);
  const placing = useMethaneStore((s) => s.placingArea);
  const setPlacing = useMethaneStore((s) => s.setPlacingArea);
  const source = useMethaneStore((s) => s.sourcePoint);
  const setSource = useMethaneStore((s) => s.setSourcePoint);
  const placingSource = useMethaneStore((s) => s.placingSource);
  const setPlacingSource = useMethaneStore((s) => s.setPlacingSource);
  if (!area) return null;

  const px = analysisAreaPx(area.sizeKm);
  const sourceOutside = source !== null && !isInAnalysisArea(area, source.lat, source.lon);
  return (
    <div className="analysis-area">
      <div className="analysis-area-head">
        <span>Analysis area</span>
        <button
          className={placing ? "ghost place-button active" : "ghost place-button"}
          title="Click the map to recentre the analysis area"
          onClick={() => setPlacing(!placing)}
        >
          {placing ? "Click map…" : "Place on map"}
        </button>
      </div>
      <CoordFields
        label="Area centre"
        value={{ lat: area.lat, lon: area.lon }}
        onCommit={(v) => v && setArea({ lat: v.lat, lon: v.lon })}
      />
      <label className="slider-row">
        Size: <b>{area.sizeKm} km</b>
        <input
          type="range"
          min={MIN_ANALYSIS_KM}
          max={MAX_ANALYSIS_KM}
          step={1}
          value={area.sizeKm}
          onChange={(e) => setArea({ sizeKm: Number(e.target.value) })}
        />
      </label>
      <p className="muted analysis-area-caption">
        {px}×{px} px @ 20 m · centre {area.lat.toFixed(4)}°, {area.lon.toFixed(4)}°
      </p>

      <div className="analysis-area-head source-head">
        <span title="Optional known emitter location. The plume must touch it (±60 m); otherwise the result is flagged source_not_in_mask instead of silently reporting the strongest blob elsewhere.">
          Source point <span className="muted">(optional)</span>
        </span>
        <button
          className={placingSource ? "ghost place-button active" : "ghost place-button"}
          title="Click the map to set the source point"
          onClick={() => setPlacingSource(!placingSource)}
        >
          {placingSource ? "Click map…" : "Pick on map"}
        </button>
      </div>
      <CoordFields label="Source" optional value={source} onCommit={setSource} />
      {sourceOutside ? (
        <p className="error-text small">
          Outside the analysis area — it would be ignored.{" "}
          <button
            className="link-button"
            onClick={() => source && setArea({ lat: source.lat, lon: source.lon })}
          >
            Centre area on source
          </button>
        </p>
      ) : (
        <p className="muted small">
          {source
            ? "The plume must touch this point (±60 m); otherwise it is flagged and the distance is shown."
            : "Without one, the strongest blob anywhere in the area is reported."}
          {source ? (
            <>
              {" "}
              <button className="link-button" onClick={() => setSource(null)}>
                clear
              </button>
            </>
          ) : null}
        </p>
      )}
    </div>
  );
}

interface MlScanState {
  status: "running" | "done" | "error";
  done: number;
  total: number;
  message: string;
  hits: number;
  detail?: string;
}

/**
 * ML candidate scan over the current site + date window. It proposes scenes
 * for review — it is not an autonomous detector; every hit lands in the feed
 * as a candidate a human still has to accept or reject. SSE UX mirrors the
 * screening dialog (local state, refetch the feed on done).
 */
function MlScanAction() {
  const qc = useQueryClient();
  const site = useMethaneStore((s) => s.selectedSite);
  const dates = useMethaneStore((s) => s.dates);
  const selectDetection = useMethaneStore((s) => s.selectDetection);
  const area = useMethaneStore((s) => s.analysisArea);
  const { data: status } = useMlStatus();
  const [maxScenes, setMaxScenes] = useState<number>(20);
  const [scan, setScan] = useState<MlScanState | null>(null);

  const running = scan?.status === "running";
  const modelReady = status?.model_loaded ?? false;
  const maxScenesValid = isIntInRange(maxScenes, 1, 200);

  const run = async () => {
    if (!site || !area || !maxScenesValid) return;
    const siteAtSubmit = site.id;
    setScan({ status: "running", done: 0, total: 0, message: "Queued", hits: 0 });
    try {
      const { job_id } = await submitMlScan({
        site_id: site.id,
        roi: analysisAreaToBBox(area),
        start: dates.start,
        end: dates.end,
        max_scenes: maxScenes,
      });
      subscribeJob(job_id, {
        onProgress: (d) =>
          setScan({
            status: "running",
            done: d.done,
            total: d.total,
            message: d.message ?? "",
            hits: 0,
          }),
        onDone: (d) => {
          const result = d.result as { detection_ids?: string[]; failed_scenes?: unknown[] };
          const ids = result.detection_ids ?? [];
          const failed = result.failed_scenes?.length ?? 0;
          setScan((prev) => ({
            status: "done",
            done: prev?.total ?? 0,
            total: prev?.total ?? 0,
            // A partial scan is still a result — but say what was not scanned.
            message:
              `${ids.length} candidate scene(s)` + (failed ? ` · ${failed} scene(s) failed` : ""),
            hits: ids.length,
          }));
          const first = ids[0];
          if (first && isStillOnSite(siteAtSubmit)) selectDetection(first);
          void qc.invalidateQueries({ queryKey: ["methane", "detections"] });
        },
        onError: (d) =>
          setScan({ status: "error", done: 0, total: 0, message: "", hits: 0, detail: d.detail }),
      });
    } catch (err) {
      const detail = describeApiError(err);
      setScan({ status: "error", done: 0, total: 0, message: "", hits: 0, detail });
    }
  };

  return (
    <div className="ml-scan">
      <div className="ml-scan-head">
        <span className="ml-badge">ML</span>
        <span>Candidate scan</span>
      </div>
      <p className="ml-scan-caption">ML candidate ranker — proposes scenes for review.</p>
      <p className="muted ml-scan-geo">
        Trained on Turkmenistan O&amp;G scenes only — expect degraded performance elsewhere.
      </p>
      <p className="muted ml-scan-window">
        Scans the current window: {dates.start} → {dates.end}
      </p>
      <label className="num-row">
        Max scenes
        <input
          type="number"
          min={1}
          max={200}
          step={1}
          value={Number.isNaN(maxScenes) ? "" : maxScenes}
          onChange={(e) => setMaxScenes(parseNumberInput(e.target.value))}
        />
      </label>
      <button
        className="ghost ml-scan-button"
        disabled={!site || !area || !modelReady || running || !maxScenesValid}
        title={modelReady ? undefined : "ML model not installed — see Settings"}
        onClick={run}
      >
        {running ? "Scanning…" : "Run ML scan"}
      </button>
      {!modelReady && status ? (
        <p className="muted ml-scan-window">Model not installed — see Settings.</p>
      ) : null}
      {scan ? (
        <div className={`run-progress ${scan.status}`}>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: scan.total ? `${(scan.done / scan.total) * 100}%` : "0%" }}
            />
          </div>
          <span className="progress-label">
            {scan.status === "error" ? `Error: ${scan.detail}` : scan.message}
          </span>
        </div>
      ) : null}
    </div>
  );
}
