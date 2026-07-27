"use client";

import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { loadDashboardData } from "@/data/clientAdapter";
import type { DashboardData, PriceBar } from "@/data/types";
import { PriceChart } from "./PriceChart";

type DashboardAppProps = {
  initialData: DashboardData;
};

type View = "live" | "charts" | "operations" | "performance";
type Theme = "light" | "dark";

const themeStorageKey = "dashboard-theme";
const themeChangeEvent = "dashboard-theme-change";

const views: { id: View; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "charts", label: "Charts" },
  { id: "operations", label: "Operations" },
  { id: "performance", label: "Performance" },
];

export function DashboardApp({ initialData }: DashboardAppProps) {
  const [data, setData] = useState(initialData);
  const [view, setView] = useState<View>("live");
  const theme = useSyncExternalStore(subscribeTheme, getThemeSnapshot, getThemeServerSnapshot);

  useEffect(() => {
    let cancelled = false;
    loadDashboardData().then((loaded) => {
      if (!cancelled) {
        setData(loaded);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const latestEquity = data.equity[0];
  const symbols = [...new Set(data.priceBars.map((bar) => bar.symbol))];
  const selectedSymbol = symbols[0] ?? data.positions[0]?.symbol ?? "QQQ";
  const selectedBars = data.priceBars.filter((bar) => bar.symbol === selectedSymbol);
  const realizedPnl = latestEquity?.realized_pnl ?? 0;
  const unrealizedPnl = latestEquity?.unrealized_pnl ?? 0;
  const freshnessReference = data.source === "fixture" ? data.generatedAt : undefined;
  const freshnessMinutes = latestEquity ? minutesSince(latestEquity.timestamp, freshnessReference) : null;
  const health = data.status === "unavailable" || (freshnessMinutes !== null && freshnessMinutes > 15) ? "degraded" : "healthy";

  return (
    <main className="dashboard-shell min-h-screen text-[var(--foreground)]" data-theme={theme}>
      <header className="border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-5 py-5 sm:px-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase text-[var(--muted)]">{data.account?.broker ?? "Fixture"} · {data.account?.mode ?? data.source}</p>
            <h1 className="mt-2 text-3xl font-bold">Live Trading Dashboard</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-md border px-3 py-2 text-sm font-semibold ${health === "healthy" ? "border-[var(--health)] text-[var(--health)]" : "border-[var(--warning)] text-[var(--warning)]"}`}>
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

        {view === "live" && <LiveView data={data} realizedPnl={realizedPnl} unrealizedPnl={unrealizedPnl} freshnessMinutes={freshnessMinutes} />}
        {view === "charts" && <ChartsView data={data} selectedSymbol={selectedSymbol} selectedBars={selectedBars} />}
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

function LiveView({ data, realizedPnl, unrealizedPnl, freshnessMinutes }: { data: DashboardData; realizedPnl: number; unrealizedPnl: number; freshnessMinutes: number | null }) {
  const latestEquity = data.equity[0];
  return (
    <section className="grid gap-5 lg:grid-cols-[1.3fr_0.9fr]">
      <div className="grid gap-4 sm:grid-cols-3 lg:col-span-2">
        <Metric label="Net liquidation" value={currency(latestEquity?.net_liquidation)} />
        <Metric label="Realized P&L" value={currency(realizedPnl)} tone={realizedPnl >= 0 ? "profit" : "loss"} />
        <Metric label="Unrealized P&L" value={currency(unrealizedPnl)} tone={unrealizedPnl >= 0 ? "profit" : "loss"} />
      </div>
      <div className="lg:col-span-2">
        <Panel title="Open positions">
          <div className="space-y-3">
            {data.positions.map((position) => (
              <div key={position.position_id} className="flex items-center justify-between border-b border-[var(--border)] pb-3 last:border-0 last:pb-0">
                <div>
                  <div className="font-semibold">{position.symbol}</div>
                  <div className="text-sm text-[var(--muted)]">{position.side} · {number(position.quantity, 0)} shares</div>
                </div>
                <div className={`text-right font-semibold ${Number(position.unrealized_pnl ?? 0) >= 0 ? "text-[var(--profit)]" : "text-[var(--loss)]"}`}>
                  {currency(position.unrealized_pnl)}
                </div>
              </div>
            ))}
            {!data.positions.length && <EmptyState label="No open positions" />}
          </div>
        </Panel>
      </div>
      <div className="lg:col-span-2">
        <Panel title="Latest order events">
          <EventTable data={data} />
        </Panel>
      </div>
      <div className="lg:col-span-2">
        <Panel title="Data freshness">
          <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Source" value={data.source} />
            <Stat label="Market state" value={data.status} />
            <Stat label="Latest equity" value={freshnessMinutes === null ? "--" : `${number(freshnessMinutes, 0)} min ago`} />
            <Stat label="Generated" value={time(data.generatedAt)} />
          </dl>
          {data.error && <p className="mt-4 rounded-md border border-[var(--warning)] p-3 text-sm text-[var(--warning)]">{data.error}</p>}
        </Panel>
      </div>
    </section>
  );
}

function ChartsView({ data, selectedSymbol, selectedBars }: { data: DashboardData; selectedSymbol: string; selectedBars: PriceBar[] }) {
  return (
    <section className="grid gap-5 lg:grid-cols-[1.5fr_0.75fr]">
      <Panel title={`${selectedSymbol} · 5m price`}>
        <PriceChart bars={selectedBars} trades={data.trades} annotations={data.annotations} symbol={selectedSymbol} />
      </Panel>
      <Panel title="Strategy annotations">
        <div className="space-y-3">
          {data.annotations.map((annotation) => (
            <div key={annotation.annotation_id} className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] p-3">
              <div className="text-sm font-semibold">{annotation.symbol} · {annotation.key}</div>
              <pre className="mt-2 overflow-x-auto text-xs text-[var(--muted)]">{JSON.stringify(annotation.value_json, null, 2)}</pre>
            </div>
          ))}
          {!data.annotations.length && <EmptyState label="No annotations" />}
        </div>
      </Panel>
    </section>
  );
}

function OperationsView({ data }: { data: DashboardData }) {
  const metrics = summarizeOperationalMetrics(data.metrics);
  return (
    <section className="grid gap-5 lg:grid-cols-2">
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
      <div className="lg:col-span-2">
        <Panel title="Recent order events">
          <EventTable data={data} />
        </Panel>
      </div>
    </section>
  );
}

function PerformanceView({ data }: { data: DashboardData }) {
  const closedTrades = data.trades.filter((trade) => trade.status === "closed");
  const pnl = data.trades.reduce((total, trade) => total + Number(trade.pnl ?? 0), 0);
  const winners = closedTrades.filter((trade) => Number(trade.pnl ?? 0) > 0).length;
  const winRate = closedTrades.length ? (winners / closedTrades.length) * 100 : null;
  return (
    <section className="grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
      <div className="grid gap-4">
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
              {data.trades.map((trade) => (
                <tr key={trade.trade_id}><td className="py-3 pr-3 font-semibold">{trade.symbol}</td><td className="py-3 pr-3">{trade.status}</td><td className="py-3 pr-3 text-right">{number(trade.quantity, 0)}</td><td className={`py-3 pr-3 text-right font-semibold ${Number(trade.pnl ?? 0) >= 0 ? "text-[var(--profit)]" : "text-[var(--loss)]"}`}>{currency(trade.pnl)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </section>
  );
}

function EventTable({ data }: { data: DashboardData }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-[820px] text-left text-sm whitespace-nowrap">
        <thead className="text-xs uppercase text-[var(--muted)]">
          <tr><th className="py-2 pr-3">Time ET</th><th className="py-2 pr-3">Symbol</th><th className="py-2 pr-3">Side</th><th className="py-2 pr-3 text-right">Qty</th><th className="py-2 pr-3">Event</th><th className="py-2 pr-3 text-right">Price</th><th className="py-2 pr-3 text-right">Slip bps</th><th className="py-2 pr-3 text-right">Latency</th></tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {data.orderEvents.map((event) => (
            <tr key={event.event_id}><td className="py-3 pr-3 text-[var(--muted)]">{time(event.occurred_at)}</td><td className="py-3 pr-3 font-semibold">{event.symbol}</td><td className="py-3 pr-3 capitalize">{event.side}</td><td className="py-3 pr-3 text-right">{number(event.quantity, 0)}</td><td className="py-3 pr-3">{event.event_type}</td><td className="py-3 pr-3 text-right">{currency(event.price)}</td><td className="py-3 pr-3 text-right">{isFillEvent(event.event_type) ? number(event.slippage_bps, 2) : "--"}</td><td className="py-3 pr-3 text-right">{event.latency_ms === null || event.latency_ms === undefined ? "--" : `${number(event.latency_ms, 0)} ms`}</td></tr>
          ))}
          {!data.orderEvents.length && <tr><td colSpan={8}><EmptyState label="No order events" /></td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function isFillEvent(eventType: string): boolean {
  return eventType === "ORDER_FILLED" || eventType === "TRADE_CLOSED";
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="surface rounded-lg border p-4"><h2 className="mb-4 text-base font-bold">{title}</h2>{children}</section>;
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