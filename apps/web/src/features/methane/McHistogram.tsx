/** Monte-Carlo Q histogram (ECharts bar, explicit component registration). */
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { MethaneHistogram } from "../../api/types";
import { histogramOption } from "../../lib/methane";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

export function McHistogram({ histogram }: { histogram: MethaneHistogram | undefined }) {
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
    chartRef.current?.setOption(histogramOption(histogram), { notMerge: true });
  }, [histogram]);

  return <div ref={ref} className="mc-histogram" data-testid="mc-histogram" />;
}
