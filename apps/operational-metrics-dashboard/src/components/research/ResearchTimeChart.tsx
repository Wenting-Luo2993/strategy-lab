"use client";

import { useEffect, useRef, useState } from "react";
import type { DailyPoint } from "@/data/researchSeries";
import { researchNumber } from "@/data/researchSeries";
import { createChartLink, dateFromTime, type ChartLink, type ChartPeer } from "@/data/linkedCharts.mjs";
import type { LogicalRange, MouseEventParams } from "lightweight-charts";

export default function ResearchTimeChart({ points, lines, calendar, link, title, unit, onRange }: {
  points: DailyPoint[];
  lines?: { key: string; name: string; color?: string; points: DailyPoint[] }[];
  calendar?: string[];
  link?: ChartLink;
  title: string;
  unit: string;
  onRange: (from: string, to: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const rangeCallback = useRef(onRange);
  const [error, setError] = useState<string | null>(null);
  const [cursorLabel, setCursorLabel] = useState("");
  useEffect(() => { rangeCallback.current = onRange; }, [onRange]);
  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    import("lightweight-charts").then(({ createChart, LineSeries, ColorType, CrosshairMode, LineStyle }) => {
      if (disposed || !container.current || !points.length) return;
      setCursorLabel("");
      const node = container.current, style = getComputedStyle(node);
      const chart = createChart(node, {
        width: node.clientWidth, height: 440,
        layout: {
          background: { type: ColorType.Solid, color: style.getPropertyValue("--surface").trim() },
          textColor: style.getPropertyValue("--muted").trim(), attributionLogo: true,
        },
        grid: { vertLines: { visible: false }, horzLines: { color: "#80808030" } },
        timeScale: { borderVisible: false, minBarSpacing: 0.001 },
        crosshair: { mode: CrosshairMode.Magnet, vertLine: { style: LineStyle.Dashed },
          horzLine: { style: LineStyle.Dashed } },
      });
      const group = link ?? createChartLink();
      if (calendar?.length) {
        chart.addSeries(LineSeries, { lastValueVisible: false, priceLineVisible: false })
          .setData(calendar.map((time) => ({ time })));
      }
      const datasets = lines ?? [{ key: "", name: "", points, color: undefined }];
      const drawn = datasets.filter((data) => data.points.length).map((data, index) => {
        const series = chart.addSeries(LineSeries, {
          title: data.name,
          color: data.color ?? ["#67a7ff", "#fb923c", "#4ade80", "#c084fc", "#f472b6"][index % 5],
          priceFormat: { type: "custom", minMove: 0.001, formatter: (v: number) => `${researchNumber(v)}${unit}` },
        });
        const values = new Map(data.points.map((p) => [p.date, p.value]));
        series.setData(data.points.map((p) => ({ time: p.date, value: p.value })));
        return { ...data, series, values };
      });
      const dates = calendar ?? [...new Set(points.map((p) => p.date))].sort();
      // Seed from data, not the library's pre-paint placeholder range.
      let viewport = { from: -0.5, to: dates.length - 0.5 };
      const peer: ChartPeer = {
        range: (range) => {
          viewport = { ...range };
          chart.timeScale().setVisibleLogicalRange(range);
        },
        cursor: (cursor) => {
          const target = cursor ? drawn.find((d) => d.key === cursor.key) : undefined;
          const value = target && cursor ? target.values.get(cursor.date) : undefined;
          if (!target || value === undefined || !cursor) {
            chart.clearCrosshairPosition(); setCursorLabel("");
          } else {
            chart.setCrosshairPosition(value, cursor.date, target.series);
            setCursorLabel(`${cursor.date} · ${target.name} · ${researchNumber(value)}${unit}`);
          }
        },
      };
      const unregister = group.register(peer, viewport);
      let resizing = false;
      const restoreViewport = () => {
        resizing = true;
        chart.timeScale().setVisibleLogicalRange(viewport);
        resizing = false;
      };
      chart.timeScale().subscribeSizeChange(restoreViewport);
      const observer = new ResizeObserver(() => {
        resizing = true;
        chart.resize(node.clientWidth, 440);
        chart.timeScale().setVisibleLogicalRange(viewport);
        resizing = false;
      });
      observer.observe(node);
      let timer: ReturnType<typeof setTimeout>;
      let interacted = false, dragging = false, pending: { from: string; to: string } | null = null;
      const schedule = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          if (!disposed && !dragging && pending) rangeCallback.current(pending.from, pending.to);
        }, 180);
      };
      const down = () => { interacted = true; dragging = true; };
      const up = () => { if (dragging) { dragging = false; schedule(); } };
      const wheel = () => { interacted = true; };
      const leave = () => group.cursor(peer, null);
      node.addEventListener("pointerdown", down, true);
      node.addEventListener("wheel", wheel, { capture: true, passive: true });
      node.addEventListener("pointerleave", leave);
      window.addEventListener("pointerup", up);
      const handler = (range: LogicalRange | null) => {
        if (resizing || !interacted || !range) return;
        viewport = { ...range };
        if (!group.range(peer, range)) return;
        const visible = chart.timeScale().getVisibleRange();
        const from = dateFromTime(visible?.from), to = dateFromTime(visible?.to);
        if (from && to) { pending = { from, to }; schedule(); }
      };
      const crosshair = (event: MouseEventParams) => {
        if (!event.sourceEvent) return;
        const date = dateFromTime(event.time);
        if (!date || !event.point) { group.cursor(peer, null); return; }
        const y = event.point.y;
        const candidates = drawn.flatMap((d) => {
          const value = d.values.get(date);
          const coordinate = value === undefined ? null : d.series.priceToCoordinate(value);
          return coordinate === null ? [] : [{ ...d, distance: Math.abs(coordinate - y) }];
        });
        const closest = candidates.find((d) => d.series === event.hoveredSeries)
          ?? candidates.sort((a, b) => a.distance - b.distance)[0];
        group.cursor(peer, closest ? { date, key: closest.key } : null);
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      chart.subscribeCrosshairMove(crosshair);
      cleanup = () => {
        clearTimeout(timer); observer.disconnect(); unregister();
        node.removeEventListener("pointerdown", down, true); node.removeEventListener("wheel", wheel, true);
        node.removeEventListener("pointerleave", leave);
        window.removeEventListener("pointerup", up);
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
        chart.timeScale().unsubscribeSizeChange(restoreViewport);
        chart.unsubscribeCrosshairMove(crosshair); chart.remove();
      };
    }).catch((reason: unknown) => {
      if (!disposed) setError(reason instanceof Error ? reason.message : "Chart library failed to load.");
    });
    return () => { disposed = true; cleanup(); };
  }, [points, lines, calendar, link, unit]);
  return <section className="mx-auto my-6 w-full max-w-6xl">
    <h4 className="mb-3 text-center font-bold">{title}</h4>
    {error ? <p role="alert">{error}</p> : !points.length
      ? <p className="p-12 text-center text-[var(--muted)]">Series unavailable for this range.</p>
      : <div ref={container} className="h-[440px] w-full" aria-label={title} />}
    <output className="block min-h-6 text-center text-sm tabular-nums">{cursorLabel}</output>
    <p className="mt-2 text-center text-xs text-[var(--muted)]">
      Drag or zoom to inspect a visible range. Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer" className="underline">TradingView</a>.
    </p>
  </section>;
}
