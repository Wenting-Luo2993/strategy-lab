"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createResearchDataAdapter } from "@/data/researchAdapter";
import { pageCount, trustBadges, type TrustBadge } from "@/data/researchPresentation";
import type {
  ResearchListQuery,
  ResearchOverview,
  ResearchPage,
  ResearchPublication,
  ResearchPublicationKind,
  ResearchRegistryRecord,
  ResearchRunSummary,
  ResearchSortField,
  SortDirection,
} from "@/data/researchTypes";
import { DashboardSectionMenu } from "@/components/DashboardSectionMenu";

type InitialResearchData = {
  overview: ResearchOverview;
  page: ResearchPage;
} | null;

const defaultQuery: ResearchListQuery = {
  page: 1,
  pageSize: 10,
  kind: "all",
  sort: "acknowledged_at",
  direction: "desc",
};
const emptyOverview: ResearchOverview = {
  total: 0,
  runSummaries: 0,
  registryRecords: 0,
  completedRuns: 0,
  validationAttention: 0,
};
const emptyPage: ResearchPage = { items: [], page: 1, pageSize: 10, total: 0, rejectedCount: 0 };
const themeStorageKey = "dashboard-theme";
const themeChangeEvent = "dashboard-theme-change";

export function ResearchDashboardApp({ initialData }: { initialData: InitialResearchData }) {
  const adapter = useMemo(() => createResearchDataAdapter(), []);
  const [overview, setOverview] = useState(initialData?.overview ?? emptyOverview);
  const [result, setResult] = useState(initialData?.page ?? emptyPage);
  const [query, setQuery] = useState(defaultQuery);
  const [selected, setSelected] = useState<ResearchPublication | null>(null);
  const [loading, setLoading] = useState(!initialData || adapter.source === "supabase");
  const [error, setError] = useState<string | null>(null);
  const theme = useSyncExternalStore<"light" | "dark">(
    subscribeTheme,
    getThemeSnapshot,
    () => "light",
  );

  useEffect(() => {
    const selectedKey = new URL(window.location.href).searchParams.get("publication");
    if (!selectedKey) return;
    adapter.getPublication(selectedKey)
      .then(setSelected)
      .catch((reason) => setError(message(reason)));
  }, [adapter]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([adapter.getOverview(), adapter.listPublications(query)])
      .then(([nextOverview, nextPage]) => {
        if (!cancelled) {
          setOverview(nextOverview);
          setResult(nextPage);
        }
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
  }, [adapter, query]);

  function updateQuery(update: Partial<ResearchListQuery>): void {
    setLoading(true);
    setError(null);
    setQuery((current) => ({ ...current, ...update, page: update.page ?? 1 }));
  }

  function selectPublication(publication: ResearchPublication): void {
    setSelected(publication);
    const url = new URL(window.location.href);
    url.searchParams.set("publication", publication.idempotencyKey);
    window.history.replaceState(null, "", url);
  }

  function closeDetail(): void {
    setSelected(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("publication");
    window.history.replaceState(null, "", url);
  }

  const totalPages = pageCount(result.total, result.pageSize);
  return (
    <main className="dashboard-shell min-h-screen text-[var(--foreground)]" data-theme={theme}>
      <DashboardSectionMenu current="research" theme={theme} />
      <header className="border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_88%,transparent)] backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-5 py-5 sm:px-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase text-[var(--muted)]">{adapter.source} · summary-only read model</p>
            <h1 className="mt-2 text-3xl font-bold">Backtest Research</h1>
            <p className="mt-2 max-w-3xl text-sm text-[var(--muted)]">
              Published methodology, validation, aggregate evidence, and lineage. Trades, fills, raw bars, ledgers, and equity curves are intentionally unavailable.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md border border-[var(--health)] px-3 py-2 text-sm font-semibold text-[var(--health)]">READ ONLY</span>
            <button className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm" onClick={() => setDashboardTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? "Light" : "Dark"}
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto w-full max-w-7xl px-5 py-6 sm:px-8">
        <section aria-label="Research overview" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <OverviewMetric label="Publications" value={overview.total} />
          <OverviewMetric label="Run summaries" value={overview.runSummaries} />
          <OverviewMetric label="Registry records" value={overview.registryRecords} />
          <OverviewMetric label="Completed runs" value={overview.completedRuns} />
          <OverviewMetric label="Needs validation review" value={overview.validationAttention} warning={overview.validationAttention > 0} />
        </section>

        <section className="surface mt-5 rounded-lg border p-4" aria-labelledby="research-explorer-title">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h2 id="research-explorer-title" className="text-lg font-bold">Publication explorer</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">Select a record to inspect its published identity and trust evidence.</p>
            </div>
            <ResearchFilters query={query} updateQuery={updateQuery} />
          </div>

          {error && <ErrorState message={error} />}
          {!error && loading && <LoadingRows />}
          {!error && !loading && result.items.length === 0 && (
            <div className="mt-5 rounded-md border border-dashed border-[var(--border)] p-8 text-center">
              <p className="font-semibold">No publications match these filters.</p>
              <p className="mt-1 text-sm text-[var(--muted)]">The remote surface contains summaries only; local debug artifacts are not published.</p>
            </div>
          )}
          {!error && !loading && result.items.length > 0 && (
            <>
              <PublicationTable publications={result.items} onSelect={selectPublication} />
              <Pagination
                page={result.page}
                totalPages={totalPages}
                total={result.total}
                pageSize={result.pageSize}
                onPage={(page) => updateQuery({ page })}
              />
            </>
          )}
        </section>
      </div>

      {selected && <PublicationDetail publication={selected} onClose={closeDetail} />}
    </main>
  );
}

function ResearchFilters({ query, updateQuery }: {
  query: ResearchListQuery;
  updateQuery: (update: Partial<ResearchListQuery>) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <label className="grid gap-1 text-xs font-semibold uppercase text-[var(--muted)]">
        Search identity
        <input
          className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm normal-case text-[var(--foreground)]"
          value={query.search ?? ""}
          onChange={(event) => updateQuery({ search: event.target.value })}
          placeholder="Run or record ID"
        />
      </label>
      <Select label="Type" value={query.kind ?? "all"} onChange={(value) => updateQuery({ kind: value as ResearchPublicationKind | "all" })} options={[
        ["all", "All"],
        ["run_summary", "Run summaries"],
        ["registry_record", "Registry records"],
      ]} />
      <Select label="Status" value={query.status ?? ""} onChange={(value) => updateQuery({ status: value || undefined })} options={[
        ["", "Any status"],
        ["completed", "Completed"],
        ["validation_failed", "Validation failed"],
        ["failed", "Failed"],
        ["validated", "Validated"],
      ]} />
      <Select label="Sort" value={query.sort ?? "acknowledged_at"} onChange={(value) => updateQuery({ sort: value as ResearchSortField })} options={[
        ["acknowledged_at", "Published"],
        ["aggregate_id", "Identity"],
        ["publication_version", "Version"],
      ]} />
      <Select label="Direction" value={query.direction ?? "desc"} onChange={(value) => updateQuery({ direction: value as SortDirection })} options={[
        ["desc", "Descending"],
        ["asc", "Ascending"],
      ]} />
    </div>
  );
}

function Select({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<[string, string]>;
}) {
  return (
    <label className="grid gap-1 text-xs font-semibold uppercase text-[var(--muted)]">
      {label}
      <select className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm normal-case text-[var(--foreground)]" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}
      </select>
    </label>
  );
}

function PublicationTable({ publications, onSelect }: {
  publications: ResearchPublication[];
  onSelect: (publication: ResearchPublication) => void;
}) {
  return (
    <div className="mt-5 overflow-x-auto">
      <table className="min-w-[920px] w-full text-left text-sm">
        <thead className="border-b border-[var(--border)] text-xs uppercase text-[var(--muted)]">
          <tr>
            <th className="px-3 py-3">Identity</th>
            <th className="px-3 py-3">Type / status</th>
            <th className="px-3 py-3">Trust</th>
            <th className="px-3 py-3">Origin</th>
            <th className="px-3 py-3">Published</th>
          </tr>
        </thead>
        <tbody>
          {publications.map((publication) => (
            <tr key={publication.idempotencyKey} className="border-b border-[var(--border)] align-top last:border-0">
              <td className="px-3 py-4">
                <button className="text-left font-semibold text-[var(--accent-strong)] underline-offset-4 hover:underline focus:underline" onClick={() => onSelect(publication)}>
                  {primaryIdentity(publication)}
                </button>
                <div className="mt-1 font-mono text-xs text-[var(--muted)]">{publication.aggregateId}</div>
              </td>
              <td className="px-3 py-4">
                <div>{publication.kind === "run_summary" ? "Run summary" : "Registry record"}</div>
                <div className="mt-1 text-xs uppercase text-[var(--muted)]">{publication.status ?? "unknown"}</div>
              </td>
              <td className="max-w-md px-3 py-4"><BadgeList badges={trustBadges(publication).slice(0, 4)} /></td>
              <td className="px-3 py-4">{publication.originDeviceId ?? "--"}</td>
              <td className="px-3 py-4">
                <div>{formatDate(publication.acknowledgedAt)}</div>
                <div className="mt-1 text-xs text-[var(--muted)]">publication v{publication.publicationVersion}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PublicationDetail({ publication, onClose }: { publication: ResearchPublication; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/45 p-3 sm:p-6" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <aside aria-label="Research publication detail" className="surface ml-auto h-full w-full max-w-3xl overflow-y-auto rounded-lg border p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase text-[var(--muted)]">{publication.kind.replace("_", " ")} · schema v{publication.schemaVersion}</p>
            <h2 className="mt-2 break-all text-2xl font-bold">{primaryIdentity(publication)}</h2>
            <p className="mt-1 break-all font-mono text-xs text-[var(--muted)]">{publication.idempotencyKey}</p>
          </div>
          <button aria-label="Close publication detail" className="rounded-md border border-[var(--border)] px-3 py-2" onClick={onClose}>Close</button>
        </div>
        <div className="mt-5"><BadgeList badges={trustBadges(publication)} /></div>
        {publication.compatibilityMessages.length > 0 && (
          <div className="mt-5 rounded-md border border-[var(--loss)] bg-[color-mix(in_srgb,var(--loss)_8%,transparent)] p-4 text-sm">
            {publication.compatibilityMessages.join(" ")}
          </div>
        )}
        <DetailIdentity publication={publication} />
        {publication.kind === "run_summary"
          ? <RunDetail publication={publication} />
          : <RegistryDetail publication={publication} />}
        <section className="mt-5 rounded-md border border-dashed border-[var(--border)] p-4 text-sm text-[var(--muted)]">
          This public record deliberately contains no trades, fills, raw bars, trade ledger, or equity curve. Evidence is represented only by aggregate counts and checksums.
        </section>
      </aside>
    </div>
  );
}

function DetailIdentity({ publication }: { publication: ResearchPublication }) {
  return (
    <section className="mt-6">
      <h3 className="font-bold">Publication identity</h3>
      <dl className="mt-3 grid gap-3 rounded-md border border-[var(--border)] p-4 text-sm sm:grid-cols-2">
        <DetailStat label="Aggregate ID" value={publication.aggregateId} mono />
        <DetailStat label="Payload SHA-256" value={publication.payloadSha256} mono />
        <DetailStat label="Published" value={formatDate(publication.acknowledgedAt)} />
        <DetailStat label="Publication version" value={String(publication.publicationVersion)} />
        <DetailStat label="Methodology" value={publication.methodologyVersion ?? "--"} />
        <DetailStat label="Origin device" value={publication.originDeviceId ?? "--"} />
      </dl>
    </section>
  );
}

function RunDetail({ publication }: { publication: ResearchRunSummary }) {
  return (
    <>
      <DetailSection title="Run and reconciliation identity">
        <DetailStat label="Strategy" value={publication.strategyId ?? "--"} />
        <DetailStat label="Run ID" value={publication.runId ?? "--"} mono />
        <DetailStat label="Lineage ID" value={publication.lineageId ?? "--"} mono />
        <DetailStat label="Code commit" value={publication.codeCommit ?? "--"} mono />
        <DetailStat label="Dataset fingerprint" value={publication.datasetFingerprint ?? "--"} mono />
        <DetailStat label="Split manifest" value={publication.splitManifestHash ?? "--"} mono />
        <DetailStat label="Created" value={publication.createdAt ? formatDate(publication.createdAt) : "--"} />
        <DetailStat label="Updated" value={publication.updatedAt ? formatDate(publication.updatedAt) : "--"} />
      </DetailSection>
      <DetailSection title="Validation">
        <DetailStat label="Profile / scope" value={`${publication.validation.profileName ?? "--"} / ${publication.validation.scope ?? "--"}`} />
        <DetailStat label="Leakage checks" value={publication.validation.leakagePassed === null ? "--" : publication.validation.leakagePassed ? "Passed" : "Failed"} />
        <DetailStat label="Findings" value={`${publication.validation.findings.blocking} blocking · ${publication.validation.findings.review} review · ${publication.validation.findings.advisory} advisory`} />
        <DetailStat label="Registry hash at execution" value={publication.validation.registryHashAtExecution ?? "--"} mono />
      </DetailSection>
      <DetailSection title="Aggregate evidence">
        <DetailStat label="Trade count" value={formatNumber(publication.evidence.aggregateTradeCount)} />
        <DetailStat label="Equity point count" value={formatNumber(publication.evidence.equityPointCount)} />
        <DetailStat label="Trade ledger checksum" value={publication.evidence.tradeLedgerSha256 ?? "--"} mono />
        <DetailStat label="Equity curve checksum" value={publication.evidence.equityCurveSha256 ?? "--"} mono />
        <DetailStat label="Session range" value={`${publication.evidence.firstSession ?? "--"} to ${publication.evidence.lastSession ?? "--"}`} />
        <DetailStat label="Retention profile" value={publication.evidence.retentionProfile ?? "--"} />
      </DetailSection>
      <section className="mt-6">
        <h3 className="font-bold">Published metrics</h3>
        {publication.metrics.length === 0 ? <p className="mt-3 text-sm text-[var(--muted)]">No aggregate metrics were published.</p> : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {publication.metrics.map((metric) => (
              <div className="rounded-md border border-[var(--border)] p-3" key={`${metric.key}:${metric.calculationVersion}`}>
                <div className="text-xs font-semibold uppercase text-[var(--muted)]">{metric.key.replaceAll("_", " ")}</div>
                <div className="mt-1 text-lg font-bold">{formatMetric(metric.value)}</div>
                <div className="mt-1 text-xs text-[var(--muted)]">calculation v{metric.calculationVersion ?? "unknown"}</div>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function RegistryDetail({ publication }: { publication: ResearchRegistryRecord }) {
  const execution = publication.executionIdentity;
  return (
    <>
      <DetailSection title="Registry identity">
        <DetailStat label="Record type" value={publication.recordType ?? "--"} />
        <DetailStat label="Record ID" value={publication.recordId ?? "--"} mono />
        <DetailStat label="Record origin" value={publication.recordOrigin ?? "--"} />
        <DetailStat label="Canonical checksum" value={publication.canonicalSha256 ?? "--"} mono />
        <DetailStat label="Recorded" value={publication.recordedAt ? formatDate(publication.recordedAt) : "--"} />
      </DetailSection>
      <DetailSection title="Execution realism identity">
        <DetailStat label="Entry fill policy" value={execution.entryFillPolicy ?? "--"} />
        <DetailStat label="Intrabar resolution" value={execution.intrabarExitResolution ?? "--"} />
        <DetailStat label="Gap fill policy" value={execution.gapFillPolicy ?? "--"} />
        <DetailStat label="Buying power enforced" value={execution.enforceBuyingPower === null ? "--" : execution.enforceBuyingPower ? "Yes" : "No"} />
        <DetailStat label="Max gross leverage" value={formatNumber(execution.maxGrossLeverage)} />
        <DetailStat label="Commission / slippage" value={`${execution.commissionModel ?? "--"} / ${execution.exitSlippageModel ?? "--"}`} />
      </DetailSection>
      <section className="mt-6">
        <h3 className="font-bold">Lineage</h3>
        {publication.lineage.length === 0 ? <p className="mt-3 text-sm text-[var(--muted)]">No published parent relationships.</p> : (
          <ul className="mt-3 space-y-2 text-sm">
            {publication.lineage.map((relation) => (
              <li className="rounded-md border border-[var(--border)] p-3" key={`${relation.relation}:${relation.recordType}:${relation.recordId}`}>
                <span className="font-semibold">{relation.relation}</span> → {relation.recordType}:{relation.recordId}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mt-6"><h3 className="font-bold">{title}</h3><dl className="mt-3 grid gap-3 rounded-md border border-[var(--border)] p-4 text-sm sm:grid-cols-2">{children}</dl></section>;
}

function DetailStat({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="min-w-0"><dt className="text-xs font-semibold uppercase text-[var(--muted)]">{label}</dt><dd className={`mt-1 break-all ${mono ? "font-mono text-xs" : "font-semibold"}`}>{value}</dd></div>;
}

function BadgeList({ badges }: { badges: TrustBadge[] }) {
  return <div className="flex flex-wrap gap-2">{badges.map((badge, index) => <span key={`${badge.label}:${index}`} className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${badgeClass(badge.tone)}`}>{badge.label}</span>)}</div>;
}

function Pagination({ page, totalPages, total, pageSize, onPage }: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return (
    <div className="mt-5 flex flex-col gap-3 border-t border-[var(--border)] pt-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-[var(--muted)]">Showing {first}-{last} of {total}</p>
      <div className="flex items-center gap-2">
        <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm disabled:opacity-40" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <span className="text-sm font-semibold">Page {page} of {totalPages}</span>
        <button className="rounded-md border border-[var(--border)] px-3 py-2 text-sm disabled:opacity-40" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}

function OverviewMetric({ label, value, warning = false }: { label: string; value: number; warning?: boolean }) {
  return <div className="surface rounded-lg border p-4"><div className="text-xs font-semibold uppercase text-[var(--muted)]">{label}</div><div className={`mt-2 text-2xl font-bold ${warning ? "text-[var(--warning)]" : ""}`}>{value.toLocaleString()}</div></div>;
}

function LoadingRows() {
  return <div aria-label="Loading research publications" className="mt-5 grid gap-3">{[1, 2, 3, 4].map((item) => <div key={item} className="h-20 animate-pulse rounded-md border border-[var(--border)] bg-[var(--surface-muted)]" />)}</div>;
}

function ErrorState({ message: errorMessage }: { message: string }) {
  return <div className="mt-5 rounded-md border border-[var(--loss)] bg-[color-mix(in_srgb,var(--loss)_8%,transparent)] p-5"><p className="font-semibold">Research data is unavailable.</p><p className="mt-1 text-sm text-[var(--muted)]">{errorMessage}</p></div>;
}

function primaryIdentity(publication: ResearchPublication): string {
  if (publication.kind === "run_summary") {
    return publication.displayAlias ?? publication.runId ?? publication.aggregateId;
  }
  return publication.recordId ?? publication.aggregateId;
}

function badgeClass(tone: TrustBadge["tone"]): string {
  if (tone === "good") return "border-[var(--health)] text-[var(--health)]";
  if (tone === "warning") return "border-[var(--warning)] text-[var(--warning)]";
  if (tone === "danger") return "border-[var(--loss)] text-[var(--loss)]";
  return "border-[var(--border)] text-[var(--muted)]";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date);
}

function formatNumber(value: number | null): string {
  return value === null ? "--" : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatMetric(value: string | number | boolean | null): string {
  if (value === null) return "--";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
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

function getThemeSnapshot(): "light" | "dark" {
  const stored = window.localStorage.getItem(themeStorageKey);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function setDashboardTheme(theme: "light" | "dark"): void {
  window.localStorage.setItem(themeStorageKey, theme);
  window.dispatchEvent(new Event(themeChangeEvent));
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Research data source failed to respond.";
}
