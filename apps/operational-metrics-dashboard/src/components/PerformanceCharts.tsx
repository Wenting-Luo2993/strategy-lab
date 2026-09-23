"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { DerivedClosedTrade } from "@/data/dashboardSelectors";
import { equityCurvePoints, tradePnlPoints } from "@/data/performanceSeries";
import type { EquitySnapshot } from "@/data/types";

type PerformanceChartsProps = {
  equity: EquitySnapshot[];
  trades: DerivedClosedTrade[];
};

export function EquityCurveChart({ equity }: Pick<PerformanceChartsProps, "equity">) {
  const points = useMemo(
    () => equityCurvePoints(equity),
    [equity],
  );
  const configure = useCallback((chart: IChartApi, styles: CSSStyleDeclaration) => {
    const series = chart.addSeries(LineSeries, {
      color: cssVar(styles, "--fresh"),
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    series.setData(points.map((point) => ({
      ...point,
      time: point.time as UTCTimestamp,
    })));
  }, [points]);

  return (
    <SeriesChart
      testId="equity-curve-chart"
      emptyLabel="No equity history"
      configure={configure}
      hasData={points.length > 0}
    />
  );
}

export function TradePnlChart({ trades }: Pick<PerformanceChartsProps, "trades">) {
  const points = useMemo(
    () => tradePnlPoints(trades),
    [trades],
  );
  const configure = useCallback((chart: IChartApi, styles: CSSStyleDeclaration) => {
    const profit = cssVar(styles, "--profit");
    const loss = cssVar(styles, "--loss");
    const series = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      base: 0,
    });
    series.setData(points.map((point) => ({
      ...point,
      time: point.time as UTCTimestamp,
      color: point.value >= 0 ? profit : loss,
    })));
  }, [points]);

  return (
    <SeriesChart
      testId="trade-pnl-chart"
      emptyLabel="No closed-trade P&L"
      configure={configure}
      hasData={points.length > 0}
    />
  );
}

function SeriesChart({
  configure,
  emptyLabel,
  hasData,
  testId,
}: {
  configure: (chart: IChartApi, styles: CSSStyleDeclaration) => void;
  emptyLabel: string;
  hasData: boolean;
  testId: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !hasData) {
      return;
    }
    const styles = getComputedStyle(container);
    const chart = createChart(container, {
      height: 280,
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
    configure(chart, styles);
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
    };
  }, [configure, hasData]);

  if (!hasData) {
    return <div className="grid h-[280px] place-items-center text-sm text-[var(--muted)]">{emptyLabel}</div>;
  }
  return <div ref={containerRef} className="h-[280px] w-full" data-testid={testId} />;
}

function cssVar(styles: CSSStyleDeclaration, name: string): string {
  return styles.getPropertyValue(name).trim();
}
