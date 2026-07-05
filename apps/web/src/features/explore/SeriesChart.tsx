/**
 * Thin imperative ECharts wrapper: init once on a ref, setOption on data
 * change, dispose on unmount, resize via ResizeObserver. No echarts-for-react
 * — the bundle stays lean with explicit component registration.
 */
import { LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { rollingMean, type SeriesPoint } from "../../lib/series";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
  CanvasRenderer,
]);

const RAW = "Daily";
const MEAN = "7-day mean";

/** The subset of ECharts' axis-tooltip params we read. */
interface AxisTipParam {
  seriesName?: string;
  marker?: string;
  value: [number, number | null];
}

function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function fmt(value: number | null): string {
  if (value === null) return "—";
  const abs = Math.abs(value);
  return abs >= 1000 || (value !== 0 && abs < 0.01) ? value.toExponential(2) : value.toFixed(3);
}

function buildOption(points: SeriesPoint[], unit: string): EChartsCoreOption {
  const smooth = rollingMean(points, 7);
  const raw = points.map((p) => [Date.parse(p.date), p.value]);
  const mean = points.map((p, i) => [Date.parse(p.date), smooth[i] ?? null]);
  const countByTime = new Map(points.map((p) => [Date.parse(p.date), p.count]));

  return {
    animation: false,
    grid: { left: 58, right: 18, top: 26, bottom: 60 },
    legend: { data: [RAW, MEAN], top: 2, right: 10, textStyle: { color: "#8b949e" } },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#161b22",
      borderColor: "#2d333b",
      textStyle: { color: "#e6edf3" },
      formatter: (params: unknown) => {
        const rows = params as AxisTipParam[];
        const t = rows[0]?.value[0];
        if (t === undefined) return "";
        const daily = rows.find((r) => r.seriesName === RAW);
        const avg = rows.find((r) => r.seriesName === MEAN);
        const count = countByTime.get(t);
        const lines = [`<b>${isoDay(t)}</b>`];
        if (daily) lines.push(`${daily.marker ?? ""} ${fmt(daily.value[1])} ${unit}`);
        if (avg && avg.value[1] !== null) {
          lines.push(`${avg.marker ?? ""} ${fmt(avg.value[1])} (7-day)`);
        }
        if (count !== undefined) {
          lines.push(`<span style="color:#8b949e">${count.toLocaleString()} px</span>`);
        }
        return lines.join("<br/>");
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: "#2d333b" } },
      axisLabel: { color: "#8b949e" },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: unit,
      nameTextStyle: { color: "#8b949e" },
      splitLine: { lineStyle: { color: "#1c2129" } },
      axisLine: { lineStyle: { color: "#2d333b" } },
      axisLabel: { color: "#8b949e" },
    },
    dataZoom: [
      { type: "inside" },
      {
        type: "slider",
        height: 16,
        bottom: 24,
        borderColor: "#2d333b",
        fillerColor: "rgba(68,147,248,0.15)",
        handleStyle: { color: "#4493f8" },
        dataBackground: {
          lineStyle: { color: "#2d333b" },
          areaStyle: { color: "#1c2129" },
        },
        textStyle: { color: "#8b949e" },
      },
    ],
    series: [
      {
        name: RAW,
        type: "line",
        data: raw,
        showSymbol: points.length <= 60,
        symbolSize: 4,
        lineStyle: { width: 1.2, color: "#4493f8" },
        itemStyle: { color: "#4493f8" },
      },
      {
        name: MEAN,
        type: "line",
        data: mean,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 2, color: "#f0a336" },
        z: 3,
      },
    ],
  };
}

export function SeriesChart({ points, unit }: { points: SeriesPoint[]; unit: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(buildOption(points, unit), { notMerge: true });
  }, [points, unit]);

  return <div ref={ref} className="series-chart" data-testid="series-chart" />;
}
