"use client";

import { Badge, Button } from "@fluentui/react-components";
import { useEffect, useMemo, useState } from "react";
import type {
  Aggregate,
  AnalysisField,
  AnalysisQuery,
  AnalysisResult,
  FilterExpression,
  FilterOperator,
} from "@/data/researchAnalysis";
import {
  analysisLimits,
  decodeAnalysisState,
  defaultAnalysisQuery,
  encodeAnalysisState,
  sanitizeAnalysisQuery,
} from "@/data/researchAnalysis";
import type { ResearchDataAdapter } from "@/data/researchTypes";

const storageKey = "research-analysis-v1";

export function ResearchAnalyzer({ adapter }: { adapter: ResearchDataAdapter }) {
  const [catalog, setCatalog] = useState<AnalysisField[]>([]);
  const [query, setQuery] = useState<AnalysisQuery>(defaultAnalysisQuery);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [cost, setCost] = useState<Awaited<ReturnType<ResearchDataAdapter["estimateAnalysis"]>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draftFilterField, setDraftFilterField] = useState("");
  const [draftFilterOperator, setDraftFilterOperator] = useState<FilterOperator>("eq");
  const [draftFilterValue, setDraftFilterValue] = useState("");
  const [draftMeasureField, setDraftMeasureField] = useState("");
  const [draftAggregate, setDraftAggregate] = useState<Aggregate>("mean");

  useEffect(() => {
    let cancelled = false;
    adapter.getAnalysisFieldCatalog()
      .then((nextCatalog) => {
        if (cancelled) return;
        setCatalog(nextCatalog);
        const urlState = new URL(window.location.href).searchParams.get("analysis");
        const localState = safeLocalState();
        const nextQuery = urlState
          ? decodeAnalysisState(urlState, nextCatalog)
          : decodeAnalysisState(localState, nextCatalog, analysisLimits.maxLocalStateBytes);
        setQuery(nextQuery);
        setDraftFilterField(nextCatalog[0]?.field ?? "");
        const numeric = nextCatalog.find((field) => field.aggregates.includes("mean"));
        setDraftMeasureField(numeric?.field ?? "");
        return run(nextQuery, nextCatalog, adapter, setCost, setResult);
      })
      .catch((reason) => {
        if (!cancelled) setError(message(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [adapter]);

  const resultFields = useMemo(() => {
    if (result?.rows[0]) return Object.keys(result.rows[0]);
    return [...query.groupBy, ...query.screeningColumns, ...query.measures.map(measureName)];
  }, [query, result]);

  function update(next: AnalysisQuery): void {
    try {
      setQuery(sanitizeAnalysisQuery(next, catalog));
      setError(null);
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function execute(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      await run(query, catalog, adapter, setCost, setResult);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoading(false);
    }
  }

  function saveState(): void {
    const encoded = encodeAnalysisState(query);
    const localEncoded = encodeURIComponent(JSON.stringify(query));
    try {
      if (localEncoded.length <= analysisLimits.maxLocalStateBytes) {
        localStorage.setItem(storageKey, localEncoded);
      } else {
        localStorage.removeItem(storageKey);
      }
    } catch {
      // URL state remains available when storage is disabled.
    }
    const url = new URL(window.location.href);
    if (encoded) {
      url.searchParams.set("analysis", encoded);
    } else {
      url.searchParams.delete("analysis");
      setError(localEncoded.length <= analysisLimits.maxLocalStateBytes
        ? `Analysis exceeds the ${analysisLimits.maxUrlStateBytes}-byte URL limit; saved locally only.`
        : `Analysis exceeds URL and ${analysisLimits.maxLocalStateBytes}-byte local-state limits; simplify the view.`);
    }
    window.history.replaceState(null, "", url);
  }

  function addFilter(): void {
    const field = catalog.find((item) => item.field === draftFilterField);
    if (!field || !field.filterOperators.includes(draftFilterOperator)) return;
    const value = parseFilterValue(draftFilterValue, field, draftFilterOperator);
    const condition: FilterExpression = {
      op: draftFilterOperator,
      field: field.field,
      ...(draftFilterOperator === "exists" ? { value: draftFilterValue !== "false" } : { value }),
    };
    const root = query.filters.op === "and" || query.filters.op === "or"
      ? query.filters
      : { op: "and" as const, children: [query.filters] };
    update({ ...query, filters: { ...root, children: [...root.children, condition] } });
    setDraftFilterValue("");
  }

  function removeFilter(index: number): void {
    if (query.filters.op !== "and" && query.filters.op !== "or") return;
    update({
      ...query,
      filters: {
        ...query.filters,
        children: query.filters.children.filter((_, childIndex) => childIndex !== index),
      },
    });
  }

  function addMeasure(): void {
    const field = catalog.find((item) => item.field === draftMeasureField);
    if (!field || !field.aggregates.includes(draftAggregate)) return;
    if (query.measures.some((item) => item.field === field.field && item.aggregate === draftAggregate)) return;
    update({ ...query, measures: [...query.measures, { field: field.field, aggregate: draftAggregate }] });
  }

  return (
    <section className="surface mt-5 rounded-lg border p-4" aria-labelledby="research-analyzer-title">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="research-analyzer-title" className="text-lg font-bold">Flexible analysis workspace</h2>
            <Badge appearance="outline" color="informative">adapter-neutral</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-[var(--muted)]">
            Screen validated run summaries, group by catalog fields, aggregate compatible metrics, and inspect every chart mark in the result table.
          </p>
        </div>
        <div className="flex gap-2">
          <Button appearance="secondary" onClick={saveState}>Save view</Button>
          <Button appearance="primary" disabled={loading || !catalog.length} onClick={execute}>
            {loading ? "Running…" : "Run analysis"}
          </Button>
        </div>
      </div>

      <div className="mt-4 grid items-start gap-4 xl:grid-cols-3">
        <fieldset className="rounded-md border border-[var(--border)] p-3">
          <legend className="px-1 text-sm font-bold">Population and trust</legend>
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={query.population.trustStates.includes("current_validated")}
              onChange={(event) => update({
                ...query,
                population: {
                  ...query.population,
                  trustStates: toggleValue(query.population.trustStates, "current_validated", event.target.checked),
                },
              })}
            />
            Current validated runs
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={query.population.trustStates.includes("validation_attention")}
              onChange={(event) => update({
                ...query,
                population: {
                  ...query.population,
                  trustStates: toggleValue(query.population.trustStates, "validation_attention", event.target.checked),
                },
              })}
            />
            Validation attention
          </label>
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={query.population.includeLegacy}
              onChange={(event) => update({
                ...query,
                population: {
                  includeLegacy: event.target.checked,
                  trustStates: toggleValue(query.population.trustStates, "legacy", event.target.checked),
                },
              })}
            />
            Legacy / pre-P2b overlay
          </label>
          <p className="mt-3 text-xs text-[var(--muted)]">Legacy is excluded by default and always remains visibly distinct.</p>
        </fieldset>

        <fieldset className="rounded-md border border-[var(--border)] p-3 xl:col-span-2">
          <legend className="px-1 text-sm font-bold">Typed composable filters</legend>
          <div className="grid gap-2 sm:grid-cols-[1fr_9rem_1fr_auto]">
            <select className="control" value={draftFilterField} onChange={(event) => {
              const field = catalog.find((item) => item.field === event.target.value);
              setDraftFilterField(event.target.value);
              setDraftFilterOperator(field?.filterOperators[0] ?? "eq");
            }}>
              {catalog.map((field) => <option key={field.field} value={field.field}>{field.field}</option>)}
            </select>
            <select className="control" value={draftFilterOperator} onChange={(event) => setDraftFilterOperator(event.target.value as FilterOperator)}>
              {(catalog.find((item) => item.field === draftFilterField)?.filterOperators ?? []).map((operator) => (
                <option key={operator} value={operator}>{operator}</option>
              ))}
            </select>
            <input
              className="control"
              value={draftFilterValue}
              disabled={draftFilterOperator === "exists"}
              placeholder={draftFilterOperator === "between" ? "min,max" : "value"}
              onChange={(event) => setDraftFilterValue(event.target.value)}
            />
            <Button onClick={addFilter}>Add</Button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select
              aria-label="Filter group operation"
              className="control w-auto"
              value={query.filters.op === "or" ? "or" : "and"}
              onChange={(event) => {
                const children = query.filters.op === "and" || query.filters.op === "or"
                  ? query.filters.children : [query.filters];
                update({ ...query, filters: { op: event.target.value as "and" | "or", children } });
              }}
            >
              <option value="and">Match all (AND)</option>
              <option value="or">Match any (OR)</option>
            </select>
            {(query.filters.op === "and" || query.filters.op === "or") && query.filters.children.map((filter, index) => (
              <button
                type="button"
                className="rounded-full border border-[var(--border)] px-3 py-1 text-xs"
                key={`${index}:${JSON.stringify(filter)}`}
                onClick={() => removeFilter(index)}
                title="Remove filter"
              >
                {formatFilter(filter)} ×
              </button>
            ))}
          </div>
        </fieldset>

        <FieldMultiSelect
          title="Screening columns"
          help={`Up to ${analysisLimits.maxScreeningColumns} columns.`}
          fields={catalog.filter((field) => field.screenable)}
          selected={query.screeningColumns}
          onChange={(screeningColumns) => update({ ...query, screeningColumns })}
        />
        <FieldMultiSelect
          title="Grouping dimensions"
          help={`No fixed compare tray; estimated cardinality must stay within ${analysisLimits.maxGroups} groups.`}
          fields={catalog.filter((field) => field.groupable)}
          selected={query.groupBy}
          onChange={(groupBy) => update({ ...query, groupBy })}
        />
        <fieldset className="rounded-md border border-[var(--border)] p-3">
          <legend className="px-1 text-sm font-bold">Aggregate measures</legend>
          <div className="grid gap-2 sm:grid-cols-[1fr_7rem_auto]">
            <select className="control" value={draftMeasureField} onChange={(event) => {
              const field = catalog.find((item) => item.field === event.target.value);
              setDraftMeasureField(event.target.value);
              setDraftAggregate(field?.aggregates.find((item) => item !== "count") ?? "count");
            }}>
              {catalog.filter((field) => field.aggregates.length).map((field) => (
                <option key={field.field} value={field.field}>{field.field}</option>
              ))}
            </select>
            <select className="control" value={draftAggregate} onChange={(event) => setDraftAggregate(event.target.value as Aggregate)}>
              {(catalog.find((item) => item.field === draftMeasureField)?.aggregates ?? []).map((aggregate) => (
                <option key={aggregate} value={aggregate}>{aggregate}</option>
              ))}
            </select>
            <Button onClick={addMeasure}>Add</Button>
          </div>
          <ul className="mt-3 space-y-1 text-xs">
            {query.measures.map((measure, index) => (
              <li className="flex items-center justify-between gap-2" key={measureName(measure)}>
                <span>{measureName(measure)}</span>
                <button type="button" className="text-[var(--loss)]" onClick={() => update({
                  ...query,
                  measures: query.measures.filter((_, measureIndex) => index !== measureIndex),
                })}>Remove</button>
              </li>
            ))}
          </ul>
        </fieldset>
      </div>

      <fieldset className="mt-4 rounded-md border border-[var(--border)] p-3">
        <legend className="px-1 text-sm font-bold">Chart encodings</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          <LabeledSelect
            label="Chart"
            value={query.chart.kind}
            options={["auto", "table", "bar"]}
            onChange={(kind) => update({ ...query, chart: { ...query.chart, kind: kind as AnalysisQuery["chart"]["kind"] } })}
          />
          <LabeledSelect
            label="X / category"
            value={query.chart.x ?? ""}
            options={["", ...resultFields]}
            onChange={(x) => update({ ...query, chart: { ...query.chart, x: x || undefined } })}
          />
          <LabeledSelect
            label="Y / value"
            value={query.chart.y ?? ""}
            options={["", ...resultFields]}
            onChange={(y) => update({ ...query, chart: { ...query.chart, y: y || undefined } })}
          />
        </div>
      </fieldset>

      {error && <div role="alert" className="mt-4 rounded-md border border-[var(--loss)] p-3 text-sm text-[var(--loss)]">{error}</div>}
      {cost && <CostSummary cost={cost} />}
      {result && (
        <>
          <AnalysisChart query={query} result={result} />
          <AnalysisTable result={result} />
        </>
      )}
    </section>
  );
}

function FieldMultiSelect({ title, help, fields, selected, onChange }: {
  title: string;
  help: string;
  fields: AnalysisField[];
  selected: string[];
  onChange: (fields: string[]) => void;
}) {
  return (
    <fieldset className="rounded-md border border-[var(--border)] p-3">
      <legend className="px-1 text-sm font-bold">{title}</legend>
      <select
        multiple
        className="control h-36"
        value={selected}
        onChange={(event) => onChange([...event.target.selectedOptions].map((option) => option.value))}
      >
        {fields.map((field) => (
          <option key={field.field} value={field.field}>
            {field.field} ({field.cardinality})
          </option>
        ))}
      </select>
      <p className="mt-2 text-xs text-[var(--muted)]">{help} Ctrl/Cmd-click to select multiple.</p>
    </fieldset>
  );
}

function CostSummary({ cost }: {
  cost: Awaited<ReturnType<ResearchDataAdapter["estimateAnalysis"]>>;
}) {
  return (
    <div className={`mt-4 grid gap-2 rounded-md border p-3 text-sm sm:grid-cols-4 ${cost.allowed ? "border-[var(--border)]" : "border-[var(--loss)]"}`}>
      <div><strong>{cost.candidateRows}</strong><span className="ml-1 text-[var(--muted)]">candidate rows</span></div>
      <div><strong>{cost.estimatedGroups}</strong><span className="ml-1 text-[var(--muted)]">estimated groups</span></div>
      <div><strong>{cost.estimatedCost}</strong><span className="ml-1 text-[var(--muted)]">cost units</span></div>
      <div>{cost.allowed ? "Within policy" : "Narrow query"} · {cost.limits.maxResponseRows} row response cap</div>
    </div>
  );
}

function AnalysisChart({ query, result }: { query: AnalysisQuery; result: AnalysisResult }) {
  const kind = query.chart.kind === "auto"
    ? query.groupBy.length && query.measures.length ? "bar" : "table"
    : query.chart.kind;
  if (kind === "table" || !result.rows.length) return null;
  if (kind !== "bar") {
    return (
      <p className="mt-4 rounded-md border border-[var(--border)] p-3 text-sm text-[var(--muted)]">
        The {kind.replaceAll("_", " ")} encoding is preserved in the analysis contract, but this build does not render it without a reviewed chart implementation. The inspectable table remains authoritative.
      </p>
    );
  }
  const x = query.chart.x ?? query.groupBy[0] ?? Object.keys(result.rows[0])[0];
  const y = query.chart.y ?? (query.measures[0] ? measureName(query.measures[0]) : undefined);
  if (!x || !y) {
    return <p className="mt-4 text-sm text-[var(--muted)]">Assign X and Y encodings to render this chart.</p>;
  }
  const marks = result.rows.slice(0, result.metadata.chartMarks);
  const values = marks.map((row) => typeof row[y] === "number" ? row[y] : 0);
  const max = Math.max(...values.map((value) => Math.abs(value)), 1);
  return (
    <div className="mt-4 rounded-md border border-[var(--border)] p-4" aria-label={`${kind} chart`}>
      <div className="flex items-center justify-between text-sm">
        <strong>{kind.replaceAll("_", " ")}</strong>
        <span className="text-xs text-[var(--muted)]">{marks.length} marks · {x} → {y}</span>
      </div>
      <div className="mt-4 flex h-52 items-end gap-2 overflow-x-auto border-b border-l border-[var(--border)] px-2">
        {marks.map((row, index) => {
          const value = values[index];
          return (
            <div className="group flex min-w-10 flex-1 flex-col items-center justify-end" key={`${String(row[x])}:${index}`}>
              <span className="mb-1 hidden text-[10px] group-hover:block">{formatCell(value)}</span>
              <div
                className="w-full max-w-14 rounded-t bg-[var(--accent-strong)]"
                style={{ height: `${Math.max(2, Math.abs(value) / max * 170)}px`, opacity: value < 0 ? 0.55 : 0.9 }}
                title={`${String(row[x])}: ${formatCell(value)}`}
              />
              <span className="max-w-20 truncate py-1 text-[10px]">{String(row[x] ?? "--")}</span>
            </div>
          );
        })}
      </div>
      {result.metadata.chartMarksTruncated && <p className="mt-2 text-xs text-[var(--loss)]">Chart marks truncated at {result.metadata.chartMarks}.</p>}
    </div>
  );
}

function AnalysisTable({ result }: { result: AnalysisResult }) {
  const columns = result.rows[0] ? Object.keys(result.rows[0]) : [];
  return (
    <div className="mt-4">
      <div className="flex items-center justify-between">
        <h3 className="font-bold">Inspectable result table</h3>
        <span className="text-xs text-[var(--muted)]">{result.metadata.returnedRows} of {result.metadata.totalResultRows} rows</span>
      </div>
      {result.metadata.truncated && (
        <p className="mt-2 rounded-md border border-[var(--loss)] p-2 text-xs text-[var(--loss)]">
          Result is explicitly truncated by row or byte policy. Narrow filters or aggregate before drawing conclusions.
        </p>
      )}
      <div className="mt-2 max-h-96 overflow-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
            <tr>{columns.map((column) => <th className="border-b border-[var(--border)] px-2 py-2" key={column}>{column}</th>)}</tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr className="border-b border-[var(--border)]" key={index}>
                {columns.map((column) => <td className="px-2 py-2" key={column}>{formatCell(row[column])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LabeledSelect({ label, value, options, onChange }: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-1 text-xs font-semibold uppercase text-[var(--muted)]">
      {label}
      <select className="control normal-case" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option || "none"} value={option}>{option || "None"}</option>)}
      </select>
    </label>
  );
}

async function run(
  query: AnalysisQuery,
  catalog: AnalysisField[],
  adapter: ResearchDataAdapter,
  setCost: (cost: Awaited<ReturnType<ResearchDataAdapter["estimateAnalysis"]>>) => void,
  setResult: (result: AnalysisResult) => void,
): Promise<void> {
  const normalized = sanitizeAnalysisQuery(query, catalog);
  const estimate = await adapter.estimateAnalysis(normalized);
  setCost(estimate);
  if (!estimate.allowed) throw new Error("Analysis cost exceeds policy. Narrow filters or grouping.");
  setResult(await adapter.runAnalysis(normalized));
}

function parseFilterValue(value: string, field: AnalysisField, operator: FilterOperator) {
  const values = ["in", "not_in", "between"].includes(operator) ? value.split(",") : [value];
  const parsed = values.map((item) => {
    const trimmed = item.trim();
    if (field.dataType === "number" || field.dataType === "integer") {
      const number = Number(trimmed);
      if (!Number.isFinite(number)) throw new Error(`Expected a numeric value for ${field.field}.`);
      return number;
    }
    if (field.dataType === "boolean") return trimmed.toLowerCase() === "true";
    return trimmed;
  });
  return ["in", "not_in", "between"].includes(operator) ? parsed : parsed[0];
}

function formatFilter(filter: FilterExpression): string {
  if (!("field" in filter)) return `${filter.op.toUpperCase()} group`;
  return `${filter.field} ${filter.op} ${Array.isArray(filter.value) ? filter.value.join(",") : String(filter.value ?? "")}`;
}

function measureName(measure: { field: string; aggregate: Aggregate }): string {
  return `${measure.aggregate}:${measure.field}`;
}

function toggleValue(values: string[], value: string, selected: boolean): string[] {
  return selected ? [...new Set([...values, value])] : values.filter((item) => item !== value);
}

function safeLocalState(): string | null {
  try {
    const value = localStorage.getItem(storageKey);
    return value && value.length <= analysisLimits.maxLocalStateBytes ? value : null;
  } catch {
    return null;
  }
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "--";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Research analysis failed.";
}
