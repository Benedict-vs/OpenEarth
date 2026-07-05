import type { Legend as LegendData } from "../../api/types";

function formatValue(value: number, displayScale: number): string {
  const scaled = value * displayScale;
  const magnitude = Math.abs(scaled);
  if (magnitude !== 0 && (magnitude < 0.01 || magnitude >= 100_000)) {
    return scaled.toExponential(1);
  }
  return Number(scaled.toPrecision(3)).toString();
}

export function Legend({ legend }: { legend: LegendData }) {
  if (legend.is_rgb) return <p className="muted">True-color composite</p>;
  const gradient = `linear-gradient(to right, ${legend.palette.join(", ")})`;
  return (
    <div className="legend">
      <div className="legend-bar" style={{ background: gradient }} />
      <div className="legend-labels">
        <span>{formatValue(legend.min, legend.display_scale)}</span>
        <span className="muted">{legend.unit}</span>
        <span>{formatValue(legend.max, legend.display_scale)}</span>
      </div>
    </div>
  );
}
