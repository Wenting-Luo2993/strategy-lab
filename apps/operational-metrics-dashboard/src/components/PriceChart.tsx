"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { PriceBar, Trade } from "@/data/types";

type PriceChartProps = {
  bars: PriceBar[];
  trades: Trade[];
  symbol: string;
};

export function PriceChart({ bars, trades, symbol }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !bars.length) {
      return;
    }

    const styles = getComputedStyle(document.documentElement);
    const chart = createChart(container, {
      height: 330,
      layout: {
        background: { type: ColorType.Solid, color: cssVar(styles, "--surface") },
        textColor: cssVar(styles, "--muted"),
      },
      grid: {
        vertLines: { color: cssVar(styles, "--chart-grid") },
        horzLines: { color: cssVar(styles, "--chart-grid") },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { mode: 1 },
    });
    chartRef.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: cssVar(styles, "--profit"),
      downColor: cssVar(styles, "--loss"),
      borderVisible: false,
      wickUpColor: cssVar(styles, "--profit"),
      wickDownColor: cssVar(styles, "--loss"),
    });
    series.setData(
      bars.map((bar) => ({
        time: toChartTime(bar.bar_start),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    createSeriesMarkers(
      series,
      trades
        .filter((trade) => trade.symbol === symbol)
        .map((trade) => ({
          time: toChartTime(trade.entry_time),
          position: trade.side === "sell" ? "aboveBar" : "belowBar",
          color: trade.side === "sell" ? cssVar(styles, "--loss") : cssVar(styles, "--profit"),
          shape: trade.side === "sell" ? "arrowDown" : "arrowUp",
          text: trade.status,
        })),
    );
    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) {
        chart.applyOptions({ width });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, symbol, trades]);

  if (!bars.length) {
    return <div className="grid h-[330px] place-items-center text-sm text-[var(--muted)]">No chart data</div>;
  }

  return <div ref={containerRef} className="h-[330px] w-full" data-testid="price-chart" />;
}

function toChartTime(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function cssVar(styles: CSSStyleDeclaration, name: string): string {
  return styles.getPropertyValue(name).trim();
}