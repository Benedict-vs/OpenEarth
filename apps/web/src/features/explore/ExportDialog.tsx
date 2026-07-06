/**
 * Export a layer's current composite as GeoTIFF, PNG, or CSV.
 *
 * GeoTIFF is a background job (a large ROI streams window-by-window on the
 * server): we submit, watch window progress over the shared SSE channel, then
 * offer a download link. PNG is a synchronous render fetched as a blob. CSV is
 * the time-series result — it only exists after a series run, so we link the
 * chart panel's export or say how to produce one.
 */
import { useEffect, useRef, useState } from "react";
import { exportPngBlob, submitGeotiffExport, useCatalog } from "../../api/queries";
import { subscribeJob } from "../../api/sse";
import type { ExportGeotiffRequest, ThumbnailRequest } from "../../api/types";
import { buildTilesRequest } from "../../map/useMintLayer";
import { useAnalysisStore } from "../../stores/analysisStore";
import { useDateStore } from "../../stores/dateStore";
import type { Layer } from "../../stores/layersStore";
import { useRoiStore } from "../../stores/roiStore";

type Format = "geotiff" | "png" | "csv";

interface GeotiffState {
  status: "idle" | "running" | "done" | "error";
  jobId: string | null;
  done: number;
  total: number;
  error: string | null;
}

const GEOTIFF_IDLE: GeotiffState = {
  status: "idle",
  jobId: null,
  done: 0,
  total: 0,
  error: null,
};

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function compositeSummary(): string {
  const { mode, start, end, targetDate, halfWindowDays } = useDateStore.getState();
  return mode === "single"
    ? `Date window: ${targetDate} ± ${halfWindowDays} d`
    : `Mean composite: ${start} → ${end}`;
}

export function ExportDialog({ layer, onClose }: { layer: Layer; onClose: () => void }) {
  const { data: catalog } = useCatalog();
  const roi = useRoiStore((s) => s.roi);

  // CSV lives in the analysis drawer; surface its state so we can link or guide.
  const seriesStatus = useAnalysisStore((s) => s.status);
  const fineJobId = useAnalysisStore((s) => s.fineJobId);

  const [format, setFormat] = useState<Format>("geotiff");
  const [scaleM, setScaleM] = useState("");
  const [geotiff, setGeotiff] = useState<GeotiffState>(GEOTIFF_IDLE);
  const [pngBusy, setPngBusy] = useState(false);
  const [pngError, setPngError] = useState<string | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);

  const nativeScaleM = catalog?.find((d) => d.id === layer.dataset)?.default_scale_m ?? null;

  // Close the SSE stream if the dialog unmounts mid-export.
  useEffect(() => () => unsubRef.current?.(), []);

  // Escape closes the modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const dateParams = () => {
    const { mode, start, end, targetDate, halfWindowDays } = useDateStore.getState();
    return { mode, start, end, targetDate, halfWindowDays };
  };

  const submitGeotiff = () => {
    if (!roi) return;
    const tiles = buildTilesRequest(
      {
        dataset: layer.dataset,
        product: layer.product,
        vizOverrides: layer.vizOverrides,
        autoRange: layer.autoRange,
      },
      roi,
      dateParams(),
    );
    const parsedScale = Number.parseInt(scaleM, 10);
    // Spread carries the (server-ignored) viz field; roi is re-narrowed non-null.
    const body: ExportGeotiffRequest = {
      ...tiles,
      roi,
      ...(Number.isFinite(parsedScale) && parsedScale > 0 ? { scale_m: parsedScale } : {}),
    };

    setGeotiff({ ...GEOTIFF_IDLE, status: "running" });
    submitGeotiffExport(body)
      .then(({ job_id }) => {
        setGeotiff((s) => ({ ...s, jobId: job_id }));
        unsubRef.current = subscribeJob(job_id, {
          onProgress: (d) => setGeotiff((s) => ({ ...s, done: d.done, total: d.total })),
          onDone: () => setGeotiff((s) => ({ ...s, status: "done" })),
          onError: (d) => setGeotiff((s) => ({ ...s, status: "error", error: d.detail })),
        });
      })
      .catch((e: unknown) =>
        setGeotiff({
          ...GEOTIFF_IDLE,
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        }),
      );
  };

  const downloadPng = () => {
    setPngBusy(true);
    setPngError(null);
    const tiles = buildTilesRequest(
      {
        dataset: layer.dataset,
        product: layer.product,
        vizOverrides: layer.vizOverrides,
        autoRange: layer.autoRange,
      },
      roi,
      dateParams(),
    );
    const body: ThumbnailRequest = { ...tiles, width: 2048 };
    exportPngBlob(body)
      .then((blob) => triggerBlobDownload(blob, `${layer.dataset}_${layer.product}.png`))
      .catch((e: unknown) => setPngError(e instanceof Error ? e.message : String(e)))
      .finally(() => setPngBusy(false));
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="export-dialog"
        role="dialog"
        aria-label={`Export ${layer.label}`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="export-dialog-head">
          <strong>Export “{layer.label}”</strong>
          <button className="icon" title="Close" onClick={onClose}>
            ×
          </button>
        </header>

        <p className="muted export-summary">{compositeSummary()}</p>

        <div className="export-tabs" role="tablist">
          {(["geotiff", "png", "csv"] as const).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={format === f}
              className={format === f ? "active" : ""}
              onClick={() => setFormat(f)}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>

        {format === "geotiff" ? (
          <div className="export-pane">
            <p className="muted">Georeferenced EPSG:4326 GeoTIFF of the raw pixel values.</p>
            {roi ? null : <p className="error-text">Draw or pick a region of interest first.</p>}
            <label className="export-field">
              Resolution (m/px)
              <input
                type="number"
                min={1}
                inputMode="numeric"
                placeholder={nativeScaleM ? `native (${nativeScaleM})` : "native"}
                value={scaleM}
                onChange={(e) => setScaleM(e.target.value)}
              />
            </label>

            {geotiff.status === "idle" || geotiff.status === "error" ? (
              <button className="primary" disabled={!roi} onClick={submitGeotiff}>
                Export GeoTIFF
              </button>
            ) : null}
            {geotiff.status === "running" ? (
              <p className="muted">
                {geotiff.total > 0
                  ? `Assembling… window ${geotiff.done}/${geotiff.total}`
                  : "Submitting…"}
              </p>
            ) : null}
            {geotiff.status === "done" && geotiff.jobId ? (
              <a className="primary export-download" href={`/api/export/${geotiff.jobId}/download`}>
                ⤓ Download GeoTIFF
              </a>
            ) : null}
            {geotiff.error ? <p className="error-text">{geotiff.error}</p> : null}
          </div>
        ) : null}

        {format === "png" ? (
          <div className="export-pane">
            <p className="muted">A rendered 2048 px PNG using the current colour scale.</p>
            <button className="primary" disabled={pngBusy} onClick={downloadPng}>
              {pngBusy ? "Rendering…" : "⤓ Download PNG"}
            </button>
            {pngError ? <p className="error-text">{pngError}</p> : null}
          </div>
        ) : null}

        {format === "csv" ? (
          <div className="export-pane">
            <p className="muted">CSV is the time-series result for this layer’s region.</p>
            {seriesStatus === "done" && fineJobId ? (
              <a
                className="primary export-download"
                href={`/api/timeseries/${fineJobId}/result?format=csv`}
                download
              >
                ⤓ Download CSV
              </a>
            ) : (
              <p className="muted">
                Run a time series in the panel below the map, then download the CSV here or from
                that panel.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** Small export trigger for a layer row; owns the dialog's open state. */
export function ExportButton({ layer }: { layer: Layer }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="icon" title="Export…" onClick={() => setOpen(true)}>
        ⤓
      </button>
      {open ? <ExportDialog layer={layer} onClose={() => setOpen(false)} /> : null}
    </>
  );
}
