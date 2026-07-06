import { useEffect, useState } from "react";
import { formatCountdown } from "../../lib/time";
import { useLayersStore, type Layer } from "../../stores/layersStore";
import { ExportButton } from "./ExportDialog";
import { Legend } from "./Legend";

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function StatusBadge({ layer, now }: { layer: Layer; now: number }) {
  if (layer.status === "minting") return <span className="badge">minting…</span>;
  if (layer.status === "error")
    return (
      <span className="badge error" title={layer.error ?? undefined}>
        error
      </span>
    );
  if (layer.mint) {
    return <span className="badge">{formatCountdown(layer.mint.expiresAt - now)}</span>;
  }
  return null;
}

function LayerRow({ layer, isTop, isBottom }: { layer: Layer; isTop: boolean; isBottom: boolean }) {
  const { removeLayer, setOpacity, toggleVisible, toggleAutoRange, moveLayer } =
    useLayersStore.getState();
  const now = useNow(1000);

  return (
    <li className="layer-row">
      <div className="layer-row-head">
        <button
          className="icon"
          title={layer.visible ? "Hide layer" : "Show layer"}
          onClick={() => toggleVisible(layer.id)}
        >
          {layer.visible ? "◉" : "◎"}
        </button>
        <span className="layer-label" title={layer.label}>
          {layer.label}
        </span>
        <StatusBadge layer={layer} now={now} />
        <button
          className="icon"
          title="Move up"
          disabled={isTop}
          onClick={() => moveLayer(layer.id, 1)}
        >
          ↑
        </button>
        <button
          className="icon"
          title="Move down"
          disabled={isBottom}
          onClick={() => moveLayer(layer.id, -1)}
        >
          ↓
        </button>
        <button
          className={layer.autoRange ? "icon active" : "icon"}
          title="Auto vis-range from the composite's percentiles"
          disabled={layer.mint?.legend.is_rgb ?? false}
          onClick={() => toggleAutoRange(layer.id)}
        >
          A
        </button>
        <ExportButton layer={layer} />
        <button className="icon" title="Remove layer" onClick={() => removeLayer(layer.id)}>
          ×
        </button>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={layer.opacity}
        title={`Opacity ${(layer.opacity * 100).toFixed(0)} %`}
        onChange={(event) => setOpacity(layer.id, Number(event.target.value))}
      />
      {layer.mint ? <Legend legend={layer.mint.legend} /> : null}
      {layer.status === "error" && layer.error ? (
        <p className="muted layer-error">{layer.error}</p>
      ) : null}
    </li>
  );
}

export function LayerPanel() {
  const layers = useLayersStore((state) => state.layers);
  if (layers.length === 0) {
    return <p className="muted">No layers yet — pick a product above.</p>;
  }
  // Render top-most first, like every GIS layer panel.
  const reversed = [...layers].reverse();
  return (
    <ul className="layer-list">
      {reversed.map((layer, index) => (
        <LayerRow
          key={layer.id}
          layer={layer}
          isTop={index === 0}
          isBottom={index === reversed.length - 1}
        />
      ))}
    </ul>
  );
}
