/** Descriptive stats of the displayed series, computed client-side. */
import { seriesStats, type SeriesPoint } from "../../lib/series";

function fmt(value: number): string {
  const abs = Math.abs(value);
  if (value === 0) return "0";
  return abs >= 1000 || abs < 0.01 ? value.toExponential(2) : value.toFixed(3);
}

function signed(value: number): string {
  return (value >= 0 ? "+" : "") + fmt(value);
}

export function StatsCards({
  points,
  unit,
  rangeStart,
  rangeEnd,
}: {
  points: SeriesPoint[];
  unit: string;
  rangeStart: string;
  rangeEnd: string;
}) {
  const stats = seriesStats(points, rangeStart, rangeEnd);
  if (!stats) return null;

  const cards: { label: string; value: string; sub?: string }[] = [
    { label: "Days", value: String(stats.n), sub: `of ${stats.rangeDays}` },
    { label: "Mean ± σ", value: `${fmt(stats.mean)} ± ${fmt(stats.std)}`, sub: unit },
    { label: "Min", value: fmt(stats.min.value), sub: stats.min.date },
    { label: "Max", value: fmt(stats.max.value), sub: stats.max.date },
    { label: "Trend / yr", value: signed(stats.trendPerYear), sub: unit },
    { label: "Coverage", value: `${(stats.coverage * 100).toFixed(0)} %`, sub: "of window" },
  ];

  return (
    <div className="stats-cards">
      {cards.map((card) => (
        <div key={card.label} className="stat-card">
          <span className="stat-label">{card.label}</span>
          <span className="stat-value">{card.value}</span>
          {card.sub ? <span className="stat-sub">{card.sub}</span> : null}
        </div>
      ))}
    </div>
  );
}
