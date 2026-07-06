import { useQueryClient } from "@tanstack/react-query";
import { submitAnalyze } from "../../api/methaneQueries";
import { subscribeJob } from "../../api/sse";
import type { AnalyzeRequest } from "../../api/types";
import { useMethaneStore } from "../../stores/methaneStore";

export function RunPanel() {
  const qc = useQueryClient();
  const site = useMethaneStore((s) => s.selectedSite);
  const target = useMethaneStore((s) => s.targetSceneId);
  const params = useMethaneStore((s) => s.params);
  const setParams = useMethaneStore((s) => s.setParams);
  const job = useMethaneStore((s) => s.job);
  const setJob = useMethaneStore((s) => s.setJob);
  const selectDetection = useMethaneStore((s) => s.selectDetection);

  const running = job?.status === "running";

  const run = async () => {
    if (!site || !target) return;
    const body: AnalyzeRequest = {
      site_id: site.id,
      target_scene_id: target,
      method: params.method,
      k_sigma: params.kSigma,
      min_area_px: params.minAreaPx,
      seed: params.seed,
    };
    const { job_id } = await submitAnalyze(body);
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
        if (detId) selectDetection(detId);
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
              onClick={() => setParams({ method: m })}
            >
              {m.toUpperCase()}
            </button>
          ))}
        </div>
        <label className="slider-row">
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
        <label className="num-row">
          Min area (px)
          <input
            type="number"
            min={1}
            value={params.minAreaPx}
            onChange={(e) => setParams({ minAreaPx: Number(e.target.value) })}
          />
        </label>
        <label className="num-row">
          Seed
          <input
            type="number"
            value={params.seed}
            onChange={(e) => setParams({ seed: Number(e.target.value) })}
          />
        </label>
      </div>

      <button className="primary run-button" disabled={!site || !target || running} onClick={run}>
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
    </div>
  );
}
