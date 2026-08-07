"use client";

import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { loadDashboardData } from "@/data/clientAdapter";
import type { DashboardData, Position, PriceBar, StrategyAnnotation, StrategyConfigSummary } from "@/data/types";
import { PriceChart } from "./PriceChart";

type DashboardAppProps = {
  initialData: DashboardData;
  strategyConfig: StrategyConfigSummary | null;
};

type View = "live" | "charts" | "operations" | "performance";
type Theme = "light" | "dark";
type HealthState = "healthy" | "closed" | "degraded";
type ChartRange = "3d" | "5d" | "10d" | "30d" | "all";
type DerivedClosedTrade = {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  entryPrice: number | null;
  exitPrice: number | null;
  exitTime: string;
  pnl: number | null;
};

const themeStorageKey = "dashboard-theme";
const themeChangeEvent = "dashboard-theme-change";

const views: { id: View; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "charts", label: "Charts" },
  { id: "operations", label: "Operations" },
  { id: "performance", label: "Performance" },
];

const chartRanges: { id: ChartRange; label: string }[] = [
  { id: "3d", label: "Current + 2 days" },
  { id: "5d", label: "5 days" },
  { id: "10d", label: "10 days" },
  { id: "30d", label: "30 days" },
  { id: "all", label: "All" },
];

const orderEventPageSize = 10;

export function DashboardApp({ initialData, strategyConfig }: DashboardAppProps) {
  const isLiveDataSource = process.env.NEXT_PUBLIC_DASHBOARD_DATA_SOURCE === "supabase";
  const [data, setData] = useState(initialData);
  const [isLoading, setIsLoading] = useState(isLiveDataSource);
  const [view, setView] = useState<View>("live");
  const theme = useSyncExternalStore(subscribeTheme, getThemeSnapshot, getThemeServerSnapshot);

  useEffect(() => {
    let cancelled = false;
    if (!isLiveDataSource) {
      return () => {
        cancelled = true;
      };
    }
    loadDashboardData()
      .then((loaded) => {
        if (!cancelled) {
          setData(loaded);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setData(unavailableClientData(error instanceof Error ? error.message : "Dashboard data source failed to respond."));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isLiveDataSource]);

  const latestEquity = data.equity[0];
  const activePositions = activePositionsFor(data.positions, latestEquity?.timestamp);
  const symbols = [...new Set(data.priceBars.map((bar) => bar.symbol))];
  const selectedSymbol = symbols[0] ?? activePositions[0]?.symbol ?? "QQQ";
  const selectedBars = data.priceBars.filter((bar) => bar.symbol === selectedSymbol);
  const realizedPnl = realizedPnlFor(data);
  const unrealizedPnl = unrealizedPnlFor(latestEquity, activePositions);
  const freshnessReference = data.source === "fixture" ? data.generatedAt : undefined;
  const freshnessMinutes = latestEquity ? minutesSince(latestEquity.timestamp, freshnessReference) : null;
  const marketState = deriveMarketState(data.status);
  const health = deriveHealthState(data.status, marketState, freshnessMinutes);

  if (isLoading) {
    return <LoadingDashboard theme={theme} />;
  }

  if (isLiveDataSource && data.status === "unavailable") {
    return <ErrorDashboard theme={theme} message={data.error ?? "Dashboard data source failed to respond."} />;
  }

  return (
    <main className="dashboard-shell min-h-screen text-[var(--foreground)]" data-theme={theme}>
      <header className="border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-5 py-5 sm:px-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase text-[var(--muted)]">{data.account?.broker ?? "Fixture"} · {data.account?.mode ?? data.source}</p>
            <h1 className="mt-2 text-3xl font-bold">Live Trading Dashboard</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-md border px-3 py-2 text-sm font-semibold ${healthBadgeClass(health)}`}>
              {health.toUpperCase()}
            </span>
            <button className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm" onClick={() => setDashboardTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? "Light" : "Dark"}
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto w-full max-w-7xl px-5 py-6 sm:px-8">
        <nav className="mb-5 flex gap-2 overflow-x-auto">
          {views.map((item) => (
            <button
              key={item.id}
              onClick={() => setView(item.id)}
              className={`rounded-md border px-4 py-2 text-sm font-semibold ${view === item.id ? "border-[var(--accent)] bg-[var(--accent)] text-white" : "border-[var(--border)] bg-[var(--surface)] text-[var(--foreground)]"}`}
            >
              {item.label}
            </button>
          ))}
        </nav>

        {view === "live" && <LiveView data={data} activePositions={activePositions} realizedPnl={realizedPnl} unrealizedPnl={unrealizedPnl} freshnessMinutes={freshnessMinutes} marketState={marketState} />}
        {view === "charts" && <ChartsView data={data} selectedSymbol={selectedSymbol} selectedBars={selectedBars} strategyConfig={strategyConfig} />}
        {view === "operations" && <OperationsView data={data} />}
        {view === "performance" && <PerformanceView data={data} />}
      </div>
    </main>
  );
}

function subscribeTheme(callback: () => void): () => void {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const notify = () => callback();
  window.addEventListener("storage", notify);
  window.addEventListener(themeChangeEvent, notify);
  media.addEventListener("change", notify);
  return () => {
    window.removeEventListener("storage", notify);
    window.removeEventListener(themeChangeEvent, notify);
    media.removeEventListener("change", notify);
  };
}

function getThemeSnapshot(): Theme {
  const stored = window.localStorage.getItem(themeStorageKey);
  if (stored === "dark" || stored === "light") {
    return stored;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getThemeServerSnapshot(): Theme {
  return "light";
}

function setDashboardTheme(theme: Theme): void {
  window.localStorage.setItem(themeStorageKey, theme);
  window.dispatchEvent(new Event(themeChangeEvent));
}

function LoadingDashboard({ theme }: { theme: Theme }) {
  return (
    <main className="dashboard-shell min-h-screen text-[var(--foreground)]" data-theme={theme}>
      <header className="border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-5 py-5 sm:px-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase text-[var(--muted)]">Supabase · live</p>
            <h1 className="mt-2 text-3xl font-bold">Live Trading Dashboard</h1>
          </div>
          <span className="rounded-md border border-[var(--muted)] px-3 py-2 text-sm font-semibold text-[var(--muted)]">LOADING</span>
        </div>
      </header>
      <div className="mx-auto grid w-full max-w-7xl gap-5 px-5 py-6 sm:px-8">
        <div className="grid gap-4 sm:grid-cols-3">
          <SkeletonBlock height="88px" />
          <SkeletonBlock height="88px" />
          <SkeletonBlock height="88px" />
        </div>
        <SkeletonBlock height="180px" />
        <SkeletonBlock height="330px" />
      </div>
    </main>
  );
}

function ErrorDashboard({ theme, message }: { theme: Theme; message: string }) {
  return (
    <main className="dashboard-shell min-h-screen text-[var(--foreground)]" data-theme={theme}>
      <div className="mx-auto grid min-h-screen w-full max-w-3xl place-items-center px-5 py-10">
        <section className="surface rounded-lg border p-6">
          <p className="text-xs font-semibold uppercase text-[var(--warning)]">Live data unavailable</p>
          <h1 className="mt-2 text-2xl font-bold">Dashboard data source failed</h1>
          <p className="mt-3 text-sm text-[var(--muted)]">{message}</p>
        </section>
      </div>
    </main>
  );
}

function SkeletonBlock({ height }: { height: string }) {
  return <div className="surface animate-pulse rounded-lg border bg-[var(--surface-muted)]" style={{ height }} />;
}

function LiveView({ data, activePositions, realizedPnl, unrealizedPnl, freshnessMinutes, marketState }: { data: DashboardData; activePositions: Position[]; realizedPnl: number; unrealizedPnl: number; freshnessMinutes: number | null; marketState: DashboardData["status"] }) {
  const latestEquity = data.equity[0];
  return (
    <section className="grid min-w-0 gap-5 lg:grid-cols-[1.3fr_0.9fr]">
      <div className="grid min-w-0 gap-4 sm:grid-cols-3 lg:col-span-2">
        <Metric label="Net liquidation" value={currency(latestEquity?.net_liquidation)} />
        <Metric label="Realized P&L" value={currency(realizedPnl)} tone={realizedPnl >= 0 ? "profit" : "loss"} />
        <Metric label="Unrealized P&L" value={currency(unrealizedPnl)} tone={unrealizedPnl >= 0 ? "profit" : "loss"} />
      </div>
      <div className="min-w-0 lg:col-span-2">
        <Panel title="Open positions">
          <div className="space-y-3">
            {activePositions.map((position) => (
              <div key={position.position_id} className="flex items-center justify-between border-b border-[var(--border)] pb-3 last:border-0 last:pb-0">
                <div>
                  <div className="font-semibold">{position.symbol}</div>
                  <div className="text-sm text-[var(--muted)]">{position.side} · {number(position.quantity, 0)} shares</div>
                  <div className="text-xs text-[var(--muted)]">Updated {time(position.updated_at)}</div>
                </div>
                <div className={`text-right font-semibold ${Number(position.unrealized_pnl ?? 0) >= 0 ? "text-[var(--profit)]" : "text-[var(--loss)]"}`}>
                  {currency(position.unrealized_pnl)}
                </div>
              </div>
            ))}
            {!activePositions.length && <EmptyState label="No open positions" />}
          </div>
        </Panel>
      </div>
      <div className="min-w-0 lg:col-span-2">
        <Panel title="Latest order events">
          <EventTable data={data} />
        </Panel>
      </div>
      <div className="min-w-0 lg:col-span-2">
        <Panel title="Data freshness">
          <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Source" value={data.source} />
            <Stat label="Market state" value={marketState} />
            <Stat label="Latest equity" value={freshnessMinutes === null ? "--" : `${number(freshnessMinutes, 0)} min ago`} />
            <Stat label="Generated" value={time(data.generatedAt)} />
          </dl>
          {data.error && <p className="mt-4 rounded-md border border-[var(--warning)] p-3 text-sm text-[var(--warning)]">{data.error}</p>}
        </Panel>
      </div>
    </section>
  );
}

function ChartsView({ data, selectedSymbol, selectedBars, strategyConfig }: { data: DashboardData; selectedSymbol: string; selectedBars: PriceBar[]; strategyConfig: StrategyConfigSummary | null }) {
  const [chartRange, setChartRange] = useState<ChartRange>("3d");
  const [fullscreen, setFullscreen] = useState(false);
  const visibleBars = filterBarsForRange(selectedBars, chartRange);
  const visibleAnnotations = latestCompleteAnnotationsForSymbol(data.annotations, selectedSymbol);
  const visibleOrderEvents = orderEventsForBars(data.orderEvents, selectedSymbol, visibleBars);

  return (
    <section className="grid min-w-0 gap-5 lg:grid-cols-[1.5fr_0.75fr]">
      <Panel title={`${selectedSymbol} · 5m price`}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm text-[var(--muted)]">
            Range
            <select
              value={chartRange}
              onChange={(event) => setChartRange(event.target.value as ChartRange)}
              className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[var(--foreground)]"
            >
              {chartRanges.map((range) => <option key={range.id} value={range.id}>{range.label}</option>)}
            </select>
          </label>
          <button className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm font-semibold" onClick={() => setFullscreen(true)}>
            Fullscreen
          </button>
        </div>
        <PriceChart bars={visibleBars} orderEvents={visibleOrderEvents} annotations={visibleAnnotations} symbol={selectedSymbol} />
      </Panel>
      <Panel title="Strategy definition">
        <StrategySummary data={data} selectedSymbol={selectedSymbol} selectedBars={selectedBars} visibleAnnotations={visibleAnnotations} strategyConfig={strategyConfig} />
      </Panel>
      {fullscreen && (
        <div className="fixed inset-0 z-50 bg-[rgba(0,0,0,0.72)] p-3 sm:p-6">
          <div className="surface flex h-full min-w-0 flex-col rounded-lg border p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-base font-bold">{selectedSymbol} · 5m price</h2>
              <button className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm font-semibold" onClick={() => setFullscreen(false)}>
                Close
              </button>
            </div>
            <PriceChart bars={visibleBars} orderEvents={visibleOrderEvents} annotations={visibleAnnotations} symbol={selectedSymbol} height="calc(100vh - 150px)" />
          </div>
        </div>
      )}
    </section>
  );
}

function OperationsView({ data }: { data: DashboardData }) {
  const metrics = summarizeOperationalMetrics(data.metrics);
  return (
    <section className="grid min-w-0 gap-5 lg:grid-cols-2">
      <Panel title="Execution quality">
        <dl className="grid gap-3 text-sm">
          <Stat label="Average latency" value={unit(metrics.avgLatency, 0, "ms")} />
          <Stat label="Worst latency" value={unit(metrics.maxLatency, 0, "ms")} />
          <Stat label="Average slippage" value={`${number(metrics.avgSlippage, 2)} bps`} />
          <Stat label="Worst slippage" value={`${number(metrics.maxSlippage, 2)} bps`} />
        </dl>
      </Panel>
      <Panel title="Publish status">
        <dl className="grid gap-3 text-sm">
          {Object.entries(data.publishStatus).map(([key, value]) => <Stat key={key} label={key.replace("_", " ")} value={number(value, 0)} />)}
        </dl>
      </Panel>
      <div className="min-w-0 lg:col-span-2">
        <Panel title="Operational data">
          <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Order events" value={number(data.orderEvents.length, 0)} />
            <Stat label="Price bars" value={number(data.priceBars.length, 0)} />
            <Stat label="Annotations" value={number(data.annotations.length, 0)} />
            <Stat label="Metrics" value={number(data.metrics.length, 0)} />
          </dl>
        </Panel>
      </div>
    </section>
  );
}

function PerformanceView({ data }: { data: DashboardData }) {
  const closedTrades = closedTradesFromOrderEvents(data.orderEvents);
  const pnl = closedTrades.reduce((total, trade) => total + Number(trade.pnl ?? 0), 0);
  const tradesWithPnl = closedTrades.filter((trade) => trade.pnl !== null);
  const winners = tradesWithPnl.filter((trade) => Number(trade.pnl) > 0).length;
  const winRate = tradesWithPnl.length ? (winners / tradesWithPnl.length) * 100 : null;
  return (
    <section className="grid min-w-0 gap-5 lg:grid-cols-[0.8fr_1.2fr]">
      <div className="grid min-w-0 gap-4">
        <Metric label="Total P&L" value={currency(pnl)} tone={pnl >= 0 ? "profit" : "loss"} />
        <Metric label="Closed trades" value={number(closedTrades.length, 0)} />
        <Metric label="Win rate" value={winRate === null ? "--" : `${number(winRate, 1)}%`} />
      </div>
      <Panel title="Trade summary">
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="text-xs uppercase text-[var(--muted)]">
              <tr><th className="py-2 pr-3">Symbol</th><th className="py-2 pr-3">Status</th><th className="py-2 pr-3 text-right">Qty</th><th className="py-2 pr-3 text-right">P&L</th></tr>
            </thead>
            <tbody className="divide-y divide-[var(--border)]">
              {closedTrades.map((trade) => (
                <tr key={trade.id}><td className="py-3 pr-3 font-semibold">{trade.symbol}</td><td className="py-3 pr-3">closed</td><td className="py-3 pr-3 text-right">{number(trade.quantity, 0)}</td><td className={`py-3 pr-3 text-right font-semibold ${Number(trade.pnl ?? 0) >= 0 ? "text-[var(--profit)]" : "text-[var(--loss)]"}`}>{currency(trade.pnl)}</td></tr>
              ))}
              {!closedTrades.length && <tr><td colSpan={4}><EmptyState label="No closed trades" /></td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
    </section>
  );
}

function EventTable({ data }: { data: DashboardData }) {
  const [page, setPage] = useState(0);
  const events = [...data.orderEvents].sort((left, right) => new Date(right.occurred_at).getTime() - new Date(left.occurred_at).getTime());
  const totalPages = Math.max(1, Math.ceil(events.length / orderEventPageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const pageEvents = events.slice(currentPage * orderEventPageSize, (currentPage + 1) * orderEventPageSize);

  return (
    <div>
      <div className="w-full max-w-full overflow-x-auto">
        <table className="min-w-[820px] text-left text-sm whitespace-nowrap">
          <thead className="text-xs uppercase text-[var(--muted)]">
            <tr><th className="py-2 pr-3">Time ET</th><th className="py-2 pr-3">Symbol</th><th className="py-2 pr-3">Side</th><th className="py-2 pr-3 text-right">Qty</th><th className="py-2 pr-3">Event</th><th className="py-2 pr-3 text-right">Price</th><th className="py-2 pr-3 text-right">Slip bps</th><th className="py-2 pr-3 text-right">Latency</th></tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {pageEvents.map((event) => (
              <tr key={event.event_id}><td className="py-3 pr-3 text-[var(--muted)]">{time(event.occurred_at)}</td><td className="py-3 pr-3 font-semibold">{event.symbol}</td><td className="py-3 pr-3 capitalize">{event.side}</td><td className="py-3 pr-3 text-right">{number(event.quantity, 0)}</td><td className="py-3 pr-3">{event.event_type}</td><td className="py-3 pr-3 text-right">{currency(event.price)}</td><td className="py-3 pr-3 text-right">{isFillEvent(event.event_type) ? number(event.slippage_bps, 2) : "--"}</td><td className="py-3 pr-3 text-right">{event.latency_ms === null || event.latency_ms === undefined ? "--" : `${number(event.latency_ms, 0)} ms`}</td></tr>
            ))}
            {!events.length && <tr><td colSpan={8}><EmptyState label="No order events" /></td></tr>}
          </tbody>
        </table>
      </div>
      {events.length > orderEventPageSize && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-[var(--muted)]">
          <span>Showing {currentPage * orderEventPageSize + 1}-{Math.min(events.length, (currentPage + 1) * orderEventPageSize)} of {events.length}</span>
          <div className="flex gap-2">
            <button className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-semibold disabled:opacity-50" disabled={currentPage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</button>
            <button className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-semibold disabled:opacity-50" disabled={currentPage >= totalPages - 1} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}>Next</button>
          </div>
        </div>
      )}
    </div>
  );
}

function StrategySummary({ data, selectedSymbol, selectedBars, visibleAnnotations, strategyConfig }: { data: DashboardData; selectedSymbol: string; selectedBars: PriceBar[]; visibleAnnotations: StrategyAnnotation[]; strategyConfig: StrategyConfigSummary | null }) {
  const latestAnnotation = visibleAnnotations[0] ?? data.annotations.find((annotation) => annotation.symbol === selectedSymbol);
  const latestTrade = data.trades.find((trade) => trade.symbol === selectedSymbol && trade.strategy);
  const strategyName = strategyConfig?.name ?? latestAnnotation?.strategy ?? latestTrade?.strategy ?? "Unknown";
  const tradingDay = latestAnnotation?.trading_day ?? "--";
  const timeframe = strategyConfig?.timeframe ?? selectedBars[0]?.timeframe ?? "--";
  const rulesetVersion = strategyConfig?.version ?? "--";

  return (
    <dl className="grid gap-3 text-sm">
      <Stat label="Strategy" value={strategyName} />
      <Stat label="Type" value={strategyConfig?.strategyType ?? "--"} />
      <Stat label="Symbol" value={selectedSymbol} />
      <Stat label="Timeframe" value={timeframe} />
      <Stat label="Trading day" value={tradingDay} />
      <Stat label="Ruleset version" value={rulesetVersion} />
      <Stat label="Position sizing" value={strategyConfig ? `${strategyConfig.positionSizeMethod}${strategyConfig.maxShares ? ` · max ${strategyConfig.maxShares}` : ""}` : "--"} />
      <Stat label="Chart levels" value={visibleAnnotations.length ? visibleAnnotations.map((annotation) => annotation.key.replace("orb_", "ORB ")).join(" / ") : "--"} />
    </dl>
  );
}

function activePositionsFor(positions: Position[], latestEquityTimestamp: string | undefined): Position[] {
  return positions
    .filter((position) => Math.abs(Number(position.quantity)) > 0)
    .filter((position) => latestEquityTimestamp === undefined || minutesSince(position.updated_at, latestEquityTimestamp) <= 20)
    .sort((left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime());
}

function filterBarsForRange(bars: PriceBar[], range: ChartRange): PriceBar[] {
  if (range === "all" || bars.length === 0) {
    return bars;
  }
  const days = Number(range.replace("d", ""));
  const latest = bars.reduce((latestTime, bar) => Math.max(latestTime, new Date(bar.bar_start).getTime()), 0);
  const cutoff = latest - (days - 1) * 24 * 60 * 60 * 1000;
  return bars.filter((bar) => new Date(bar.bar_start).getTime() >= cutoff);
}

function latestCompleteAnnotationsForSymbol(annotations: StrategyAnnotation[], symbol: string): StrategyAnnotation[] {
  const symbolAnnotations = annotations.filter((annotation) => annotation.symbol === symbol && annotation.enabled);
  const byDay = new Map<string, StrategyAnnotation[]>();
  symbolAnnotations.forEach((annotation) => {
    const existing = byDay.get(annotation.trading_day) ?? [];
    existing.push(annotation);
    byDay.set(annotation.trading_day, existing);
  });
  const latestCompleteDay = [...byDay.entries()]
    .filter(([, dayAnnotations]) => hasLevelAnnotation(dayAnnotations, "orb_high") && hasLevelAnnotation(dayAnnotations, "orb_low"))
    .sort(([left], [right]) => right.localeCompare(left))[0];
  return latestCompleteDay ? latestCompleteDay[1].filter((annotation) => annotation.key === "orb_high" || annotation.key === "orb_low") : [];
}

function hasLevelAnnotation(annotations: StrategyAnnotation[], key: string): boolean {
  return annotations.some((annotation) => annotation.key === key && typeof annotation.value_json.price === "number");
}

function orderEventsForBars(orderEvents: DashboardData["orderEvents"], symbol: string, bars: PriceBar[]): DashboardData["orderEvents"] {
  if (bars.length === 0) {
    return [];
  }
  const first = new Date(bars[0].bar_start).getTime();
  const last = new Date(bars[bars.length - 1].bar_start).getTime();
  return orderEvents.filter((event) => event.symbol === symbol && isChartMarkerEvent(event.event_type) && isBetween(new Date(event.occurred_at).getTime(), first, last));
}

function isChartMarkerEvent(eventType: string): boolean {
  return eventType === "ORDER_FILLED" || eventType === "TRADE_CLOSED";
}

function closedTradesFromOrderEvents(orderEvents: DashboardData["orderEvents"]): DerivedClosedTrade[] {
  const closeEvents = orderEvents
    .filter((event) => event.event_type === "TRADE_CLOSED")
    .sort((left, right) => new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime());
  const closeOrderIds = new Set(closeEvents.map((event) => event.broker_order_id));
  const entryEvents = orderEvents
    .filter((event) => event.event_type === "ORDER_FILLED" && !closeOrderIds.has(event.broker_order_id))
    .sort((left, right) => new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime());
  const usedEntryEventIds = new Set<string>();

  return closeEvents.map((closeEvent) => {
    const entryEvent = findEntryEventForClose(closeEvent, entryEvents, usedEntryEventIds);
    if (entryEvent) {
      usedEntryEventIds.add(entryEvent.event_id);
    }
    const pnl = realizedPnlFromEvents(entryEvent, closeEvent);
    return {
      id: closeEvent.event_id,
      symbol: closeEvent.symbol,
      side: entryEvent?.side ?? closeEvent.side,
      quantity: closeEvent.quantity,
      entryPrice: entryEvent?.price ?? null,
      exitPrice: closeEvent.price,
      exitTime: closeEvent.occurred_at,
      pnl,
    };
  }).reverse();
}

function findEntryEventForClose(closeEvent: DashboardData["orderEvents"][number], entryEvents: DashboardData["orderEvents"], usedEntryEventIds: Set<string>): DashboardData["orderEvents"][number] | undefined {
  const closeTime = new Date(closeEvent.occurred_at).getTime();
  return [...entryEvents]
    .reverse()
    .find((entryEvent) => {
      if (usedEntryEventIds.has(entryEvent.event_id)) {
        return false;
      }
      const sameTrade = closeEvent.trade_id && entryEvent.trade_id === closeEvent.trade_id;
      const plausiblePriorEntry = entryEvent.symbol === closeEvent.symbol && new Date(entryEvent.occurred_at).getTime() <= closeTime;
      return Boolean(sameTrade) || plausiblePriorEntry;
    });
}

function realizedPnlFromEvents(entryEvent: DashboardData["orderEvents"][number] | undefined, closeEvent: DashboardData["orderEvents"][number]): number | null {
  if (!entryEvent || entryEvent.price === null || closeEvent.price === null) {
    return null;
  }
  const quantity = closeEvent.quantity || entryEvent.quantity;
  if (entryEvent.side === "sell" || entryEvent.side === "short") {
    return (entryEvent.price - closeEvent.price) * quantity;
  }
  return (closeEvent.price - entryEvent.price) * quantity;
}

function realizedPnlFor(data: DashboardData): number {
  const brokerValue = data.equity[0]?.realized_pnl;
  if (brokerValue !== null && brokerValue !== undefined && Number.isFinite(brokerValue) && brokerValue !== 0) {
    return brokerValue;
  }
  const eventValue = closedTradesFromOrderEvents(data.orderEvents).reduce((total, trade) => total + Number(trade.pnl ?? 0), 0);
  if (eventValue !== 0) {
    return eventValue;
  }
  const tradeValue = data.trades
    .filter((trade) => trade.status === "closed")
    .reduce((total, trade) => total + Number(trade.pnl ?? 0), 0);
  return tradeValue !== 0 ? tradeValue : brokerValue ?? 0;
}

function unrealizedPnlFor(latestEquity: DashboardData["equity"][number] | undefined, activePositions: Position[]): number {
  const brokerValue = latestEquity?.unrealized_pnl;
  if (brokerValue !== null && brokerValue !== undefined && Number.isFinite(brokerValue) && brokerValue !== 0) {
    return brokerValue;
  }
  const positionValue = activePositions.reduce((total, position) => total + Number(position.unrealized_pnl ?? 0), 0);
  return positionValue !== 0 ? positionValue : brokerValue ?? 0;
}

function isBetween(value: number, min: number, max: number): boolean {
  return value >= min && value <= max;
}

function isFillEvent(eventType: string): boolean {
  return eventType === "ORDER_FILLED" || eventType === "TRADE_CLOSED";
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="surface min-w-0 rounded-lg border p-4"><h2 className="mb-4 text-base font-bold">{title}</h2>{children}</section>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" }) {
  return <div className="surface rounded-lg border p-4"><div className="text-xs font-semibold uppercase text-[var(--muted)]">{label}</div><div className={`mt-2 text-2xl font-bold ${tone === "profit" ? "text-[var(--profit)]" : tone === "loss" ? "text-[var(--loss)]" : ""}`}>{value}</div></div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between gap-4"><dt className="capitalize text-[var(--muted)]">{label}</dt><dd className="text-right font-semibold">{value}</dd></div>;
}

function EmptyState({ label }: { label: string }) {
  return <div className="rounded-md border border-dashed border-[var(--border)] p-6 text-center text-sm text-[var(--muted)]">{label}</div>;
}

function summarizeOperationalMetrics(metrics: DashboardData["metrics"]) {
  const latency = valuesFor(metrics, "latency_ms");
  const slippage = valuesFor(metrics, "slippage_bps");
  return {
    avgLatency: average(latency),
    maxLatency: latency.length ? Math.max(...latency) : null,
    avgSlippage: average(slippage),
    maxSlippage: slippage.length ? Math.max(...slippage) : null,
  };
}

function valuesFor(metrics: DashboardData["metrics"], name: string): number[] {
  return metrics.filter((metric) => metric.metric_name === name).map((metric) => metric.metric_value).filter(Number.isFinite);
}

function average(values: number[]): number | null {
  return values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
}

function minutesSince(timestamp: string, referenceTimestamp?: string): number {
  const referenceTime = referenceTimestamp ? new Date(referenceTimestamp).getTime() : Date.now();
  return Math.max(0, (referenceTime - new Date(timestamp).getTime()) / 60000);
}

function deriveHealthState(status: DashboardData["status"], marketState: DashboardData["status"], freshnessMinutes: number | null): HealthState {
  if (status === "unavailable") {
    return "degraded";
  }
  if (marketState === "closed") {
    return "closed";
  }
  return freshnessMinutes !== null && freshnessMinutes > 15 ? "degraded" : "healthy";
}

function deriveMarketState(status: DashboardData["status"]): DashboardData["status"] {
  if (status === "unavailable" || status === "empty") {
    return status;
  }
  return isRegularMarketOpen() ? "live" : "closed";
}

function isRegularMarketOpen(now = new Date()): boolean {
  const marketParts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const weekday = marketParts.find((part) => part.type === "weekday")?.value;
  if (weekday === "Sat" || weekday === "Sun") {
    return false;
  }
  const hour = Number(marketParts.find((part) => part.type === "hour")?.value ?? "0");
  const minute = Number(marketParts.find((part) => part.type === "minute")?.value ?? "0");
  const minutes = hour * 60 + minute;
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

function healthBadgeClass(health: HealthState): string {
  if (health === "healthy") {
    return "border-[var(--health)] text-[var(--health)]";
  }
  if (health === "closed") {
    return "border-[var(--muted)] text-[var(--muted)]";
  }
  return "border-[var(--warning)] text-[var(--warning)]";
}

function currency(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "--";
  }
  return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function number(value: number | null | undefined, digits: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "--";
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function unit(value: number | null | undefined, digits: number, suffix: string): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "--" : `${number(value, digits)} ${suffix}`;
}

function time(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "America/New_York",
  }).format(new Date(value));
}

function unavailableClientData(error: string): DashboardData {
  return {
    source: "supabase",
    status: "unavailable",
    generatedAt: new Date().toISOString(),
    account: null,
    equity: [],
    positions: [],
    orderEvents: [],
    priceBars: [],
    trades: [],
    metrics: [],
    annotations: [],
    publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 0 },
    error,
  };
}