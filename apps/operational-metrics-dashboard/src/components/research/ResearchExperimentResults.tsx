"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { ResearchDataAdapter, ResearchExperimentEvent } from "@/data/researchTypes";
import { calendarYearRanges, comparisonCurves, cumulativeR, detailCache, numeric, rangeSeries, record, researchNumber,
  type ResearchDailySeries } from "@/data/researchSeries";
import { combinedComparison, createChartLink } from "@/data/linkedCharts.mjs";

const TimeChart = dynamic(() => import("./ResearchTimeChart"), { ssr: false });
const AggregateChart = dynamic(() => import("./ResearchAggregateChart"), { ssr: false });
type Detail = { events: ResearchExperimentEvent[]; series: ResearchDailySeries | null };
const caches = new WeakMap<ResearchDataAdapter, (id: string) => Promise<Detail>>();

function loader(adapter: ResearchDataAdapter) {
  let cache = caches.get(adapter);
  if (!cache) {
    cache = detailCache(async (id) => {
      const [events, series] = await Promise.all([
        adapter.getExperimentPublications(id), adapter.getExperimentDailySeries(id),
      ]);
      return { events, series };
    });
    caches.set(adapter, cache);
  }
  return cache;
}

export default function ResearchExperimentResults({ adapter, experimentId }: {
  adapter: ResearchDataAdapter; experimentId: string;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState(0);
  const [range, setRange] = useState({ from: "", to: "" });
  const [retry, setRetry] = useState(0);
  const [hidden, setHidden] = useState<string[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [link] = useState(createChartLink);
  useEffect(() => {
    let active = true;
    loader(adapter)(experimentId).then((value) => {
      if (active) setDetail(value);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Experiment detail request failed.");
    });
    return () => { active = false; };
  }, [adapter, experimentId, retry]);
  const outcome = record(detail?.events.filter((e) => e.kind === "experiment_outcome").at(-1)?.eventData.outcome);
  const result = record(outcome.result);
  const definition = record(detail?.events.find((e) => e.kind === "experiment_definition")?.eventData.definition);
  const runs = detail?.series?.runs ?? [];
  const run = runs[selectedRun];
  const dates = [...new Set(runs.flatMap((r) => [...r.dailyR, ...r.dailyEquity, ...r.dailyPrices, ...r.dailyClosedEquity]).map((p) => p.date))].sort();
  const years = calendarYearRanges(dates);
  const from = range.from || dates[0] || "", to = range.to || dates.at(-1) || "";
  const validRange = from <= to;
  const computed = run && validRange ? rangeSeries(run, from, to) : null;
  const colors = ["#67a7ff", "#fb923c", "#4ade80", "#c084fc", "#f472b6"];
  const visible = validRange ? runs.map((r, index) => ({
    key: r.runId, name: r.label, color: colors[index % colors.length], ...comparisonCurves(r, from, to),
  })).filter((r) => !hidden.includes(r.key)) : [];
  const canCombine = !!run && runs.every((r) => r.candidateId === runs[0].candidateId
    && (definition.kind === "fixed_rule" || r.foldIndex === runs[0].foldIndex) && r.segmentRole === runs[0].segmentRole)
    && new Set(runs.map((r) => r.label)).size === runs.length;
  if (showAll && canCombine && validRange) {
    const within = (points: { date: string; value: number }[]) => points.filter((p) => p.date >= from && p.date <= to);
    const all = combinedComparison(runs.map((r) => ({
      capital: r.startingEquity, r: r.rAvailable ? within(r.dailyR) : [],
      prices: within(r.dailyPrices), closed: within(r.dailyClosedEquity),
    })));
    visible.unshift({ key: "__all__", name: "All", color: "#eab308",
      cumulative: cumulativeR(all.r), benchmark: all.benchmark, closed: all.closed });
  }
  const calendar = dates.filter((date) => date >= from && date <= to);
  const comparisonLines = (key: "benchmark" | "closed" | "cumulative" | "drawdown") => visible.map((r) => ({
    key: r.key, name: r.name, color: r.color,
    points: key === "drawdown" ? r.cumulative.map((p) => ({ date: p.date, value: p.drawdownR })) : r[key],
  }));
  function chartRange(a: string, b: string) {
    // A zoom changes the analysis window, not merely the viewport.
    if (a !== from || b !== to) setRange({ from: a, to: b });
  }
  if (error) return <section className="mt-6 rounded border border-[var(--loss)] p-4" role="alert">
    <p>Experiment details unavailable: {error}</p>
    <button className="mt-2 underline" onClick={() => { setDetail(null); setError(null); setRetry((n) => n + 1); }}>Retry</button>
  </section>;
  if (!detail) return <p className="mt-6" role="status">Loading this experiment&apos;s details…</p>;
  const pooled = record(result.pooled);
  const percentile = (value: unknown) => numeric(value) === null ? "Unavailable" : `${researchNumber(Number(value) * 100, 2)}%`;
  return <div className="mt-6">
    <h3 className="text-xl font-bold">{String(definition.title ?? "Experiment results")}</h3>
    <p className="my-3 text-sm text-[var(--muted)]">
      {String(result.evidence_role ?? definition.evidence_role ?? "Evidence role unavailable")}
      {" · "}{String(result.outcome ?? "Not assessed")}. Exploration and synthetic previews cannot confirm an edge.
    </p>
    <p className="my-3">Net expectancy {researchNumber(pooled.mean_r)}R
      {" · "}95% CI [{researchNumber(pooled.ci_low_r)}, {researchNumber(pooled.ci_high_r)}]R
      {" · "}Minimum edge {researchNumber(result.min_edge_r)}R</p>
    <h4 className="mt-5 font-bold">Performance by member</h4>
    <p className="my-2 text-sm text-[var(--muted)]">Independent accounts and segment roles are not a shared portfolio. Stored full-history drawdown may be intraday; charts below use daily session observations.</p>
    {runs.length ? <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr>{["Member", "Role / fold", "Trades", "Expectancy R", "95% CI R", "Sharpe", "Max DD", "DD days", "Net-dollar PF", "Net P&L"].map((h) => <th className="p-3" key={h}>{h}</th>)}</tr></thead>
      <tbody>{runs.map((r) => {
        const estimate = record(record(result.by_symbol)[r.label]);
        return <tr className="border-t border-[var(--border)]" key={`${r.runId}:${r.foldIndex}:${r.candidateId}:${r.segmentRole}`}>
          <td className="p-3">{r.label}</td><td>{r.segmentRole} / {r.foldIndex}</td><td>{r.tradeCount}</td>
          <td>{researchNumber(estimate.mean_r ?? r.netExpectancyR ?? r.metrics.expectancy_r)}</td>
          <td>[{researchNumber(estimate.ci_low_r)}, {researchNumber(estimate.ci_high_r)}]</td>
          <td>{researchNumber(r.metrics.sharpe_ratio)}</td><td>{percentile(r.metrics.max_drawdown)}</td>
          <td>{r.metrics.max_drawdown_duration_days ?? "Unavailable"}</td><td>{researchNumber(r.profitFactor)}</td>
          <td>{researchNumber(r.metrics.total_pnl ?? r.metrics.net_pnl, 2)}</td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="my-4">Daily member read model has not been published. Existing outcome diagnostics remain available below.</p>}
    {run && <section className="mt-6">
      <div className="flex flex-wrap items-end justify-center gap-4">
        <label>Member<select className="ml-2 rounded border p-2 bg-[var(--surface)]" value={selectedRun} onChange={(e) => {
          setSelectedRun(Number(e.target.value)); setRange({ from: "", to: "" });
        }}>{runs.map((r, i) => <option value={i} key={i}>{r.label} · {r.candidateId} · {r.segmentRole} · fold {r.foldIndex}</option>)}</select></label>
        <label>From<input aria-label="Research range start" type="date" className="ml-2 rounded border p-2" value={from} onChange={(e) => setRange({ from: e.target.value, to })} /></label>
        <label>To<input aria-label="Research range end" type="date" className="ml-2 rounded border p-2" value={to} onChange={(e) => setRange({ from, to: e.target.value })} /></label>
        <button className="rounded border p-2" onClick={() => setRange({ from: "", to: "" })}>All history</button>
      </div>
      {years.length > 0 && <div className="mt-3 flex flex-wrap justify-center gap-2" role="group" aria-label="Calendar year">
        {years.map((window) => {
          const active = from === window.from && to === window.to;
          return <button key={window.year} type="button" aria-pressed={active}
            className={`rounded border px-3 py-2 ${active ? "border-[var(--accent)] bg-[var(--surface)] font-semibold" : ""}`}
            onClick={() => setRange({ from: window.from, to: window.to })}>{window.year}</button>;
        })}
      </div>}
      <div className="my-4 flex flex-wrap justify-center gap-4" role="group" aria-label="Visible tickers">
        <label className="inline-flex items-center gap-2 whitespace-nowrap">
          <input type="checkbox" className="m-0 h-4 w-4 shrink-0 p-0" checked={showAll} disabled={!canCombine}
            onChange={(e) => setShowAll(e.target.checked)} /><span>All</span>
        </label>
        {runs.map((r) => <label className="inline-flex items-center gap-2 whitespace-nowrap" key={r.runId}>
          <input type="checkbox" className="m-0 h-4 w-4 shrink-0 p-0" checked={!hidden.includes(r.runId)}
          onChange={(e) => setHidden((old) => e.target.checked ? old.filter((id) => id !== r.runId) : [...old, r.runId])} /><span>{r.label}</span></label>)}
      </div>
      <p className="my-2 text-center text-sm text-[var(--muted)]">{canCombine
        ? "All sums every independent account, including hidden tickers. Dollars start at the sum of their capitals and R is summed; this is not a shared-capital portfolio backtest."
        : "All is unavailable across different candidates, folds, roles, or repeated ticker accounts."}</p>
      <p className="text-center text-sm text-[var(--muted)]">Independent accounts. Benchmark and reconstructed closed-trade equity rebase to each account&apos;s starting capital at the first selected daily close. Price ratios do not explicitly credit dividends or charge benchmark fees. Closed-trade equity is not intraday mark-to-market.</p>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {([["benchmark", "Buy-and-hold benchmarks", " USD"], ["cumulative", "Cumulative net R", "R"],
          ["closed", "Reconstructed closed-trade equity", " USD"], ["drawdown", "Drawdown in R", "R"]] as const).map(([key, title, unit]) => {
          const lines = comparisonLines(key);
          return <TimeChart key={key} title={title} unit={unit} points={lines.flatMap((l) => l.points)} lines={lines} calendar={calendar} link={link} onRange={chartRange} />;
        })}
      </div>
      {!validRange && <p role="alert" className="mt-3">Start date must not be after end date.</p>}
      {computed && <>
        <p className="my-4 text-center" role="status">{visible.length ? visible.map((r) =>
          `${r.name}: selected-range cumulative ${researchNumber(r.cumulative.at(-1)?.value)}R · max drawdown ${researchNumber(r.cumulative.length ? Math.min(0, ...r.cumulative.map((p) => p.drawdownR)) : null)}R`).join(" · ") : "No tickers selected."}</p>
        <p className="text-center text-sm text-[var(--muted)]">Cumulative R and drawdown in R restart at zero for the selected window. No artificial portfolio aggregation.</p>
        {!run.equityAvailable && <p className="my-5 rounded border p-4 text-center">Intraday equity points were not retained. The reconstructed daily closed-trade curve above is derived separately from complete trade exits.</p>}
        <details className="my-4 text-xs"><summary>Daily series provenance</summary><p>{detail.series?.basis}</p><p>Run: {run.runId}</p><p>Trades: {run.tradeChecksum}</p><p>Equity: {run.equityChecksum}</p><p>{run.priceBasis}</p><p>Price file: {run.priceChecksum}</p></details>
      </>}
    </section>}
    <DiagnosticSection title="Net R distribution" value={result.r_histogram} histogram />
    {["by_year", "by_symbol", "by_side", "by_exit_reason", "cost", "power", "robustness", "dollars", "conditions", "paired", "summary"].map((key) =>
      <DiagnosticSection key={key} title={key.replaceAll("_", " ")} value={result[key]} />)}
    {Object.entries(result).filter(([key]) => !["pooled", "r_histogram", "by_year", "by_symbol", "by_side", "by_exit_reason", "cost", "power", "robustness", "dollars", "conditions", "paired", "summary"].includes(key))
      .map(([key, value]) => <details className="mt-4" key={key}><summary>{key.replaceAll("_", " ")}</summary><Evidence value={value} /></details>)}
    <details className="mt-5"><summary>Frozen methodology and execution definition</summary><Evidence value={definition} /></details>
    <details className="mt-5"><summary>Experiment timeline</summary>{detail.events.map((event) =>
      <p className="my-2 text-sm" key={event.idempotencyKey}>{event.kind.replaceAll("_", " ")} · {event.status ?? "Not assessed"} · {event.recordedAt}</p>)}</details>
    <p className="mt-5 text-sm text-[var(--muted)]">Only published aggregates are shown. Individual excursions, trades, fills, and market bars are not fetched.</p>
  </div>;
}

function DiagnosticSection({ title, value, histogram = false }: { title: string; value: unknown; histogram?: boolean }) {
  const data = record(value);
  if (!Object.keys(data).length) return null;
  let labels: string[] = [], values: number[] = [];
  if (histogram && Array.isArray(data.counts) && Array.isArray(data.edges)) {
    const edges = data.edges;
    const counts = data.counts;
    labels = counts.map((_, i) => i === 0 ? `< ${researchNumber(edges[0])}` : i === counts.length - 1
      ? `≥ ${researchNumber(edges.at(-1))}` : `${researchNumber(edges[i - 1])} to ${researchNumber(edges[i])}`);
    values = data.counts.map((v) => numeric(v) ?? 0);
  } else if (title === "by year" || title === "by symbol") {
    const pairs = Object.entries(data).filter(([, v]) => numeric(record(v).mean_r) !== null);
    labels = pairs.map(([k]) => k); values = pairs.map(([, v]) => Number(record(v).mean_r));
  }
  return <section className="mt-6"><h4 className="font-bold capitalize">{title}</h4>
    {values.length > 0 && <AggregateChart title={title} labels={labels} values={values} />}
    <Evidence value={value} />
  </section>;
}

function Evidence({ value }: { value: unknown }): React.ReactNode {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value !== "object") return numeric(value) !== null ? researchNumber(value) : String(value);
  return <div className="my-2 overflow-x-auto"><table className="w-full text-left text-sm"><tbody>
    {Object.entries(value).map(([key, item]) => <tr key={key} className="border-t border-[var(--border)]">
      <th className="p-2 align-top font-medium">{key.replaceAll("_", " ")}</th><td className="p-2"><Evidence value={item} /></td>
    </tr>)}
  </tbody></table></div>;
}
