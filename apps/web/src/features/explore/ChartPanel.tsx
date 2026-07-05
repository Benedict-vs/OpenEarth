/**
 * Collapsible time-series drawer under the map. Picks the topmost visible
 * ready layer by default, runs a coarse+native series over the shared ROI and
 * dates, and shows the progressive coarse→fine fill plus client-side stats.
 */
import { useEffect } from "react";
import { useCatalog } from "../../api/queries";
import { mergeCoarseFine } from "../../lib/series";
import { useMapContext } from "../../map/MapContext";
import { useAnalysisStore } from "../../stores/analysisStore";
import { useDateStore } from "../../stores/dateStore";
import { useLayersStore } from "../../stores/layersStore";
import { useRoiStore } from "../../stores/roiStore";
import { SeriesChart } from "./SeriesChart";
import { StatsCards } from "./StatsCards";

export function ChartPanel() {
  const { map } = useMapContext();

  const open = useAnalysisStore((s) => s.open);
  const status = useAnalysisStore((s) => s.status);
  const coarse = useAnalysisStore((s) => s.coarse);
  const fine = useAnalysisStore((s) => s.fine);
  const progress = useAnalysisStore((s) => s.progress);
  const fineJobId = useAnalysisStore((s) => s.fineJobId);
  const error = useAnalysisStore((s) => s.error);
  const storeLayerId = useAnalysisStore((s) => s.layerId);
  const range = useAnalysisStore((s) => s.range);
  const run = useAnalysisStore((s) => s.run);
  const selectLayer = useAnalysisStore((s) => s.selectLayer);
  const toggleOpen = useAnalysisStore((s) => s.toggleOpen);

  const layers = useLayersStore((s) => s.layers);
  const roi = useRoiStore((s) => s.roi);
  const start = useDateStore((s) => s.start);
  const end = useDateStore((s) => s.end);
  const { data: catalog } = useCatalog();

  // Default target = topmost (last) visible ready layer, else topmost layer.
  const topDown = [...layers].reverse();
  const defaultLayer = topDown.find((l) => l.visible && l.status === "ready") ?? topDown[0];
  const selectedId = storeLayerId ?? defaultLayer?.id ?? null;
  const selected = layers.find((l) => l.id === selectedId) ?? null;

  const product = catalog
    ?.find((d) => d.id === selected?.dataset)
    ?.products.find((p) => p.key === selected?.product);
  const unit = product?.display_unit ?? "";
  const displayScale = product?.display_scale ?? 1;

  const displaySeries = mergeCoarseFine(coarse, fine).map((p) => ({
    ...p,
    value: p.value * displayScale,
  }));

  // Opening/closing the drawer changes the map's height — resize after layout.
  useEffect(() => {
    if (!map) return;
    const frame = requestAnimationFrame(() => map.resize());
    return () => cancelAnimationFrame(frame);
  }, [open, map]);

  const canRun = Boolean(selected && roi) && status !== "running";
  const onRun = () => {
    if (!selected || !roi) return;
    void run({
      dataset: selected.dataset,
      product: selected.product,
      label: selected.label,
      roi,
      start,
      end,
    });
  };

  return (
    <section className={open ? "chart-drawer open" : "chart-drawer"}>
      <header className="chart-drawer-head">
        <button className="icon" onClick={toggleOpen} title={open ? "Collapse" : "Expand"}>
          {open ? "▾" : "▸"}
        </button>
        <strong>Time series</strong>
        <select
          value={selectedId ?? ""}
          onChange={(e) => selectLayer(e.target.value || null)}
          disabled={layers.length === 0}
          title="Layer to analyze"
        >
          {layers.length === 0 ? <option value="">No layers</option> : null}
          {topDown.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </select>
        <button className="primary" onClick={onRun} disabled={!canRun}>
          Run
        </button>
        {status === "running" ? (
          <span className="badge">
            {progress ? `chunk ${progress.done}/${progress.total}` : "running…"}
          </span>
        ) : null}
        <span className="chart-drawer-spacer" />
        {status === "done" && fineJobId ? (
          <a className="csv-link" href={`/api/timeseries/${fineJobId}/result?format=csv`} download>
            Export CSV
          </a>
        ) : null}
      </header>

      {open ? (
        <div className="chart-body">
          {error ? <p className="error-text">{error}</p> : null}
          {displaySeries.length === 0 && status !== "running" && !error ? (
            <p className="muted chart-hint">
              {roi
                ? "Pick a layer and press Run."
                : "Draw or pick a region of interest, then press Run."}
            </p>
          ) : null}
          {displaySeries.length > 0 ? (
            <div className="chart-content">
              <SeriesChart points={displaySeries} unit={unit} />
              <StatsCards
                points={displaySeries}
                unit={unit}
                rangeStart={range?.start ?? start}
                rangeEnd={range?.end ?? end}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
