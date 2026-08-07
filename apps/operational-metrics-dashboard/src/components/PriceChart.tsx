"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import type { OrderEvent, PriceBar, StrategyAnnotation } from "@/data/types";

type PriceChartProps = {
  bars: PriceBar[];
  orderEvents: OrderEvent[];
  annotations: StrategyAnnotation[];
  symbol: string;
  height?: number | string;
};

export function PriceChart({ bars, orderEvents, annotations, symbol, height = 330 }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !bars.length) {
      return;
    }

    const styles = getComputedStyle(container);
    const chart = createChart(container, {
      height: typeof height === "number" ? height : container.clientHeight,
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
      orderEventMarkers(orderEvents, bars, symbol, styles),
    );
    annotations.filter((annotation) => annotation.symbol === symbol).forEach((annotation) => {
      const price = annotationPrice(annotation);
      if (price === null) {
        return;
      }
      series.createPriceLine({
        price,
        color: annotation.key === "orb_low" ? cssVar(styles, "--loss") : cssVar(styles, "--fresh"),
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: annotationLabel(annotation),
      });
    });
    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) {
        chart.applyOptions({ width, height: typeof height === "number" ? height : container.clientHeight });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [annotations, bars, height, orderEvents, symbol]);

  if (!bars.length) {
    return <div className="grid place-items-center text-sm text-[var(--muted)]" style={{ height }}>No chart data</div>;
  }

  return <div ref={containerRef} className="w-full" style={{ height }} data-testid="price-chart" />;
}

function orderEventMarkers(orderEvents: OrderEvent[], bars: PriceBar[], symbol: string, styles: CSSStyleDeclaration): SeriesMarker<UTCTimestamp>[] {
  const closeOrderIds = new Set(
    orderEvents
      .filter((event) => event.event_type === "TRADE_CLOSED")
      .map((event) => event.broker_order_id),
  );
  return orderEvents
    .filter((event) => event.symbol === symbol)
    .filter((event) => event.event_type === "ORDER_FILLED" || event.event_type === "TRADE_CLOSED")
    .filter((event) => event.event_type !== "ORDER_FILLED" || !closeOrderIds.has(event.broker_order_id))
    .flatMap((event) => {
      const markerTime = markerTimeForEvent(event, bars);
      if (markerTime === null) {
        return [];
      }
      const isSell = event.side === "sell" || event.side === "short";
      const isExit = event.event_type === "TRADE_CLOSED";
      const marker: SeriesMarker<UTCTimestamp> = {
        time: markerTime,
        position: isSell ? "aboveBar" : "belowBar",
        color: isExit ? cssVar(styles, "--fresh") : isSell ? cssVar(styles, "--loss") : cssVar(styles, "--profit"),
        shape: isSell ? "arrowDown" : "arrowUp",
        text: isExit ? "Exit" : "Entry",
      };
      return [marker];
    })
    .sort((left, right) => Number(left.time) - Number(right.time));
}

function markerTimeForEvent(event: OrderEvent, bars: PriceBar[]): UTCTimestamp | null {
  const eventTime = new Date(event.occurred_at).getTime();
  const eventMarketDate = marketDate(event.occurred_at);
  const sameDayBars = bars
    .filter((bar) => marketDate(bar.bar_start) === eventMarketDate)
    .sort((left, right) => new Date(left.bar_start).getTime() - new Date(right.bar_start).getTime());
  if (!sameDayBars.length) {
    return null;
  }
  const exactBar = sameDayBars.find((bar) => new Date(bar.bar_start).getTime() === eventTime);
  if (exactBar) {
    return toChartTime(exactBar.bar_start);
  }
  const priorBar = [...sameDayBars].reverse().find((bar) => new Date(bar.bar_start).getTime() <= eventTime);
  return toChartTime((priorBar ?? sameDayBars[0]).bar_start);
}

function marketDate(value: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

function toChartTime(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function annotationPrice(annotation: StrategyAnnotation): number | null {
  const price = annotation.value_json.price;
  return typeof price === "number" && Number.isFinite(price) ? price : null;
}

function annotationLabel(annotation: StrategyAnnotation): string {
  const label = annotation.value_json.label;
  return typeof label === "string" ? label : annotation.key;
}

function cssVar(styles: CSSStyleDeclaration, name: string): string {
  return styles.getPropertyValue(name).trim();
}