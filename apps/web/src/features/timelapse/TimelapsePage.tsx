import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiGet } from "../../api/client";
import { useAois, useCatalog, usePresets } from "../../api/queries";
import { subscribeJob } from "../../api/sse";
import {
  cancelJob,
  fetchPreview,
  stillUrl,
  submitTimelapse,
  useRenderDetail,
  usePreflight,
} from "../../api/timelapseQueries";
import type {
  Render,
  RenderDetail,
  RoiIn,
  ThumbnailRequest,
  TimelapseRequest,
} from "../../api/types";
import { parseManifest } from "../../lib/manifest";
import { buildPlate, downloadBlob, plateInputFromDetail } from "../../lib/plate";
import {
  buildPreflightRequest,
  buildTimelapseRequest,
  middleWindow,
  pacingSummary,
} from "../../lib/timelapse";
import { useRoiStore } from "../../stores/roiStore";
import { useTimelapseStore } from "../../stores/timelapseStore";
import { AvailabilityTimeline } from "./AvailabilityTimeline";
import { Inspector } from "./Inspector";
import { ProgramMonitor, type LiveRun } from "./ProgramMonitor";
import { RenderGallery } from "./RenderGallery";

type DockTab = "availability" | "renders";

/** Timelapse Studio ("Cut"): an editing-suite for broadcast-quality, honest timelapse. */
export function TimelapsePage() {
  const qc = useQueryClient();
  const form = useTimelapseStore((s) => s.form);
  const { data: catalog } = useCatalog();
  const { data: aois } = useAois();
  const { data: roiPresets } = usePresets();
  const currentRoi = useRoiStore((s) => s.roi);

  const [run, setRun] = useState<LiveRun | null>(null);
  const [activeRenderId, setActiveRenderId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dockTab, setDockTab] = useState<DockTab>("availability");
  const [preview, setPreview] = useState<{ url: string; caption: string } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const previewUrlRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  const dataset = catalog?.find((d) => d.id === form.datasetId) ?? catalog?.[0] ?? null;
  const products = useMemo(
    () => dataset?.products.filter((p) => !p.requires_builder) ?? [],
    [dataset],
  );
  const productKey = form.productKey || products[0]?.key || "";
  const productIsRgb = products.find((p) => p.key === productKey)?.is_rgb ?? false;

  const roi = useMemo<RoiIn | null>(
    () => resolveRoi(form.roiSource, currentRoi, aois, roiPresets),
    [form.roiSource, currentRoi, aois, roiPresets],
  );

  // Availability probe drives the timeline, the frame count, and the native-limit readout.
  const preflightReq = useMemo(
    () => (roi && productKey ? buildPreflightRequest({ ...form, productKey }, roi) : null),
    [form, productKey, roi],
  );
  const preflightQ = usePreflight(preflightReq);
  const preflight = preflightQ.data ?? null;
  const nativeMaxDim = preflight?.native_max_dim ?? null;
  const pacing = preflight ? pacingSummary(preflight, form) : null;

  const activeDetail = useRenderDetail(activeRenderId);
  const manifest = parseManifest(activeDetail.data);
  const running = run?.status === "running";

  const revokePreview = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
  };
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      revokePreview(); // revoke the last object URL on unmount
    };
  }, []);

  const clearMonitor = () => {
    setActiveRenderId(null);
    setRun(null);
    revokePreview();
    setPreview(null);
    setError(null);
  };

  const previewWindow = preflight ? middleWindow(preflight) : null;
  const canPreview = !!roi && !!productKey && !!previewWindow;

  const runPreview = async () => {
    if (!roi || !productKey || !previewWindow) return;
    setPreviewing(true);
    setPreviewError(null);
    const body: ThumbnailRequest = {
      dataset: form.datasetId,
      product: productKey,
      roi,
      dates: previewWindow,
      composite: "mean",
      half_window_days: 3,
      // Upscaling is allowed since the native-lock reversal; the preview matches
      // the final render's look rather than stopping at the sensor limit.
      width: 1024,
      auto_range: form.visMin == null && form.visMax == null,
      viz_overrides: { vis_min: form.visMin, vis_max: form.visMax },
    };
    try {
      const blob = await fetchPreview(body);
      if (!mountedRef.current) return; // unmounted mid-fetch — don't mint a leaked URL
      revokePreview();
      const url = URL.createObjectURL(blob);
      previewUrlRef.current = url;
      setActiveRenderId(null);
      setRun(null);
      setPreview({
        url,
        caption: `Preview · ${previewWindow.start} · mean composite${
          form.composite !== "mean" ? ` (final: ${form.composite})` : ""
        }`,
      });
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setPreviewing(false);
    }
  };

  // The single submit path both the form and "Render final" flow through.
  const runJob = async (body: TimelapseRequest) => {
    const { job_id, render_id } = await submitTimelapse(body);
    revokePreview();
    setPreview(null);
    setActiveRenderId(null);
    setRun({
      jobId: job_id,
      renderId: render_id,
      status: "running",
      done: 0,
      total: 0,
      message: "Queued",
    });
    subscribeJob(job_id, {
      onProgress: (d) =>
        setRun((r) => (r ? { ...r, done: d.done, total: d.total, message: d.message } : r)),
      onDone: () => {
        setRun((r) => (r ? { ...r, status: "done" } : r));
        setActiveRenderId(render_id);
        setDockTab("renders");
        void qc.invalidateQueries({ queryKey: ["timelapse", "renders"] });
      },
      onError: (d) => setRun((r) => (r ? { ...r, status: "error", detail: d.detail } : r)),
    });
  };

  const submit = async (draft: boolean) => {
    if (!roi || !productKey || !dataset || submitting) return;
    setError(null);
    setSubmitting(true); // synchronous — closes the double-click window before the POST
    try {
      await runJob(
        buildTimelapseRequest({ ...form, datasetId: dataset.id, productKey }, roi, {
          draft,
          productIsRgb,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setRun(null);
    } finally {
      setSubmitting(false);
    }
  };

  // Re-render a draft at its intended full settings (draft off).
  const renderFinal = async (renderId: string) => {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const detail = await apiGet<RenderDetail>(`/api/timelapse/${renderId}`);
      const body = { ...(detail.params as unknown as TimelapseRequest), draft: false };
      if (body.duration_s != null) delete (body as { fps?: number }).fps; // keep duration XOR fps
      await runJob(body);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setRun(null);
    } finally {
      setSubmitting(false);
    }
  };

  const [plateBusy, setPlateBusy] = useState(false);
  const exportPlate = async (frameIndex: number) => {
    const detail = activeDetail.data;
    if (!detail) return;
    setPlateBusy(true);
    setError(null);
    try {
      const input = plateInputFromDetail(detail, frameIndex, stillUrl(detail.id, frameIndex));
      if (!input) throw new Error("This render has no manifest to build a plate from.");
      downloadBlob(
        await buildPlate(input),
        `${detail.dataset}_${detail.product}_plate_${frameIndex + 1}.png`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlateBusy(false);
    }
  };

  const selectRender = (r: Render) => {
    revokePreview();
    setPreview(null);
    setRun(null);
    setActiveRenderId(r.id);
  };

  const clipLabel = clipTimecode(roi, form.title, pacing);
  const steps = flowState(!!roi && !!productKey, running);

  return (
    <div className="cut-studio">
      <header className="cut-bbar">
        <span className="cut-rec" aria-hidden />
        <span className="cut-brand">Studio</span>
        <div className="cut-flow" aria-label="Flow">
          {steps.map((s) => (
            <span key={s.label} className={`cut-flow-step ${s.state}`}>
              {s.label}
            </span>
          ))}
        </div>
        <span className="cut-clip mono">{clipLabel}</span>
        <button
          className="mini cut-new"
          onClick={clearMonitor}
          title="Clear the monitor for a new clip"
        >
          ＋ New
        </button>
      </header>

      <div className="cut-monitor-wrap">
        <ProgramMonitor
          player={
            activeRenderId && activeDetail.data?.frame_count
              ? { renderId: activeRenderId, frameCount: activeDetail.data.frame_count, manifest }
              : null
          }
          run={run}
          onStopRun={() => {
            if (run) void cancelJob(run.jobId);
          }}
          preview={preview}
          onPreview={runPreview}
          previewing={previewing}
          previewError={previewError}
          canPreview={canPreview}
          onExportPlate={exportPlate}
          plateBusy={plateBusy}
        />
      </div>

      <Inspector
        datasets={catalog}
        products={products}
        productKey={productKey}
        productIsRgb={productIsRgb}
        aois={aois}
        roiPresets={roiPresets}
        roi={roi}
        nativeMaxDim={nativeMaxDim}
        pacing={pacing}
        running={running || submitting}
        onRender={submit}
        submitError={error}
      />

      <section className="cut-dock">
        <div className="cut-dock-tabs" role="tablist" aria-label="Dock">
          <button
            role="tab"
            id="dock-tab-availability"
            aria-controls="dock-panel"
            aria-selected={dockTab === "availability"}
            className={dockTab === "availability" ? "sel" : ""}
            onClick={() => setDockTab("availability")}
          >
            Availability
          </button>
          <button
            role="tab"
            id="dock-tab-renders"
            aria-controls="dock-panel"
            aria-selected={dockTab === "renders"}
            className={dockTab === "renders" ? "sel" : ""}
            onClick={() => setDockTab("renders")}
          >
            Renders
          </button>
        </div>
        <div
          className="cut-dock-body"
          role="tabpanel"
          id="dock-panel"
          aria-labelledby={
            dockTab === "availability" ? "dock-tab-availability" : "dock-tab-renders"
          }
        >
          {dockTab === "availability" ? (
            <AvailabilityTimeline
              preflight={preflight}
              loading={preflightQ.isFetching}
              error={
                preflightQ.error instanceof ApiError
                  ? preflightQ.error.detail
                  : preflightQ.error
                    ? String(preflightQ.error)
                    : null
              }
              primary={form.datasetId}
            />
          ) : (
            <RenderGallery
              activeId={activeRenderId}
              onSelect={selectRender}
              onRenderFinal={renderFinal}
            />
          )}
        </div>
      </section>
    </div>
  );
}

/** The four flow labels + their state for the top bar. */
function flowState(setup: boolean, running: boolean) {
  return [
    { label: "Area", state: setup ? "done" : "now" },
    { label: "Span", state: setup ? "done" : "" },
    { label: "Look", state: setup && !running ? "now" : setup ? "done" : "" },
    { label: "Render", state: running ? "now" : "" },
  ] as const;
}

function clipTimecode(
  roi: RoiIn | null,
  title: string,
  pacing: { frames: number; fps: number } | null,
): string {
  const name = title.trim() || (roi ? "Untitled clip" : "No region");
  if (!pacing) return name;
  const secs = pacing.fps > 0 ? pacing.frames / pacing.fps : 0;
  return `${name} · ${pacing.frames} frames · ${secs.toFixed(1)}s`;
}

type Aoi = { id: number; name: string; roi: RoiIn };
type Preset = { name: string; bbox: { west: number; south: number; east: number; north: number } };

/** Resolve the ROI-source key to a concrete ROI (current map / AOI / preset). */
function resolveRoi(
  source: string,
  currentRoi: RoiIn | null,
  aois: Aoi[] | undefined,
  presets: Preset[] | undefined,
): RoiIn | null {
  if (source === "current") return currentRoi;
  if (source.startsWith("aoi:")) {
    const id = Number(source.slice(4));
    return aois?.find((a) => a.id === id)?.roi ?? null;
  }
  if (source.startsWith("preset:")) {
    const name = source.slice(7);
    const p = presets?.find((x) => x.name === name);
    return p ? { kind: "bbox", ...p.bbox } : null;
  }
  return null;
}
