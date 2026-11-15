"use client";

import { useEffect, useRef, useState } from "react";
import { researchNumber } from "@/data/researchSeries";

export default function ResearchAggregateChart({ title, labels, values }: {
  title: string; labels: string[]; values: number[];
}) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    Promise.all([
      import("echarts/core"), import("echarts/charts"),
      import("echarts/components"), import("echarts/renderers"),
    ]).then(([core, charts, components, renderers]) => {
      if (disposed || !container.current) return;
      core.use([charts.BarChart, components.GridComponent, components.TooltipComponent, components.DataZoomComponent, renderers.CanvasRenderer]);
      const chart = core.init(container.current);
      const text = getComputedStyle(container.current).getPropertyValue("--muted").trim();
      chart.setOption({
        textStyle: { color: text },
        tooltip: { trigger: "axis", valueFormatter: (v: number) => researchNumber(v) },
        grid: { left: 65, right: 25, top: 25, bottom: 75 },
        xAxis: { type: "category", data: labels },
        yAxis: { type: "value", axisLabel: { formatter: (v: number) => researchNumber(v, 2) } },
        dataZoom: [{ type: "inside" }, { type: "slider", bottom: 5 }],
        series: [{ type: "bar", data: values, itemStyle: { color: "#3b82f6" } }],
      });
      const resize = new ResizeObserver(() => chart.resize());
      resize.observe(container.current);
      cleanup = () => { resize.disconnect(); chart.dispose(); };
    }).catch((reason: unknown) => {
      if (!disposed) setError(reason instanceof Error ? reason.message : "Diagnostic chart failed to load.");
    });
    return () => { disposed = true; cleanup(); };
  }, [labels, values]);
  return <section className="mx-auto my-6 w-full max-w-6xl">
    <h4 className="mb-3 text-center font-bold">{title}</h4>
    {error ? <p role="alert">{error}</p> : <div ref={container} className="h-[400px] w-full" aria-label={title} />}
  </section>;
}
