export type DailyPoint = { date: string; value: number };
export type ResearchSeriesRun = {
  runId: string;
  label: string;
  candidateId: string;
  foldIndex: number;
  segmentRole: string;
  tradeCount: number;
  profitFactor: number | null;
  netExpectancyR: number | null;
  metrics: Record<string, number | null>;
  startingEquity: number | null;
  tradeChecksum: string;
  equityChecksum: string;
  dailyR: DailyPoint[];
  dailyEquity: DailyPoint[];
  dailyClosedEquity: DailyPoint[];
  dailyPrices: DailyPoint[];
  priceBasis: string;
  priceChecksum: string;
  rAvailable: boolean;
  equityAvailable: boolean;
};
export type ResearchDailySeries = {
  version: 1;
  experimentId: string;
  basis: string;
  runs: ResearchSeriesRun[];
};

export function numeric(value: unknown): number | null {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function researchNumber(value: unknown, digits = 3): string {
  const number = numeric(value);
  return number === null ? "Unavailable" : number.toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function calendarYearRanges(dates: readonly string[]) {
  const sorted = [...dates].sort();
  const first = sorted[0], last = sorted.at(-1);
  if (!first || !last) return [];
  return [...new Set(sorted.map((date) => date.slice(0, 4)))].map((year) => ({
    year,
    from: year === first.slice(0, 4) ? first : `${year}-01-01`,
    to: year === last.slice(0, 4) ? last : `${year}-12-31`,
  }));
}

export function decodeDailySeries(value: unknown, experimentId: string): ResearchDailySeries {
  const root = record(value);
  if (root.version !== 1 || root.experimentId !== experimentId || !Array.isArray(root.runs)
      || root.runs.length > 200) throw new Error("Unsupported daily research series identity or version.");
  let count = 0;
  const points = (value: unknown, equity: boolean): DailyPoint[] => {
    if (!Array.isArray(value)) throw new Error("Daily series points must be an array.");
    count += value.length;
    if (count > 50_000) throw new Error("Daily series exceeds 50,000 points.");
    let previous = "";
    return value.map((item) => {
      const p = record(item), number = numeric(p.value);
      if (typeof p.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(p.date)
          || new Date(`${p.date}T00:00:00Z`).toISOString().slice(0, 10) !== p.date
          || p.date <= previous || number === null || (equity && number <= 0)) {
        throw new Error("Invalid or unordered daily series point.");
      }
      previous = p.date;
      return { date: p.date, value: number };
    });
  };
  return {
    version: 1, experimentId, basis: String(root.basis ?? ""),
    runs: root.runs.map((value) => {
      const row = record(value);
      if (typeof row.runId !== "string" || typeof row.candidateId !== "string"
          || !Number.isSafeInteger(row.foldIndex) || !Number.isSafeInteger(row.tradeCount)
          || typeof row.rAvailable !== "boolean" || typeof row.equityAvailable !== "boolean") {
        throw new Error("Invalid daily series member.");
      }
      const dailyR = points(row.dailyR, false), dailyEquity = points(row.dailyEquity, true);
      if (row.equityAvailable !== (dailyEquity.length > 0)) throw new Error("Equity availability mismatch.");
      return {
        runId: row.runId, label: String(row.label ?? row.runId), candidateId: row.candidateId,
        foldIndex: Number(row.foldIndex), segmentRole: String(row.segmentRole ?? ""),
        tradeCount: Number(row.tradeCount), profitFactor: numeric(row.profitFactor),
        netExpectancyR: numeric(row.netExpectancyR),
        startingEquity: numeric(row.startingEquity),
        tradeChecksum: String(row.tradeChecksum ?? ""), equityChecksum: String(row.equityChecksum ?? ""),
        metrics: Object.fromEntries(Object.entries(record(row.metrics)).map(([k, v]) => [k, numeric(v)])),
        dailyR, dailyEquity,
        dailyClosedEquity: points(row.dailyClosedEquity ?? [], false),
        dailyPrices: points(row.dailyPrices ?? [], true),
        priceBasis: String(row.priceBasis ?? ""), priceChecksum: String(row.priceChecksum ?? ""),
        rAvailable: row.rAvailable, equityAvailable: row.equityAvailable,
      };
    }),
  };
}

export function comparisonCurves(run: ResearchSeriesRun, from: string, to: string) {
  const prices = run.dailyPrices.filter((p) => p.date >= from && p.date <= to);
  const closed = run.dailyClosedEquity.filter((p) => p.date >= from && p.date <= to);
  const capital = run.startingEquity;
  const baseline = closed[0]?.value;
  return {
    benchmark: capital !== null && capital > 0 && prices.length ? prices.map((p) => ({ date: p.date, value: capital * p.value / prices[0].value })) : [],
    closed: capital !== null && capital > 0 && baseline && baseline > 0 ? closed.map((p) => ({ date: p.date, value: capital * p.value / baseline })) : [],
    cumulative: run.rAvailable ? rangeSeries(run, from, to).cumulative : [],
  };
}

export function cumulativeR(points: DailyPoint[]) {
  let sum = 0, peak = 0;
  return points.map((p) => {
    sum += p.value;
    peak = Math.max(peak, sum);
    return { date: p.date, value: sum, drawdownR: sum - peak };
  });
}

/** Recompute before charting, from complete daily data rather than display samples. */
export function rangeSeries(run: ResearchSeriesRun, from: string, to: string) {
  if (from > to) throw new Error("Range start must not be after its end.");
  const within = (p: DailyPoint) => p.date >= from && p.date <= to;
  const cumulative = cumulativeR(run.dailyR.filter(within));
  const before = run.dailyEquity.filter((p) => p.date < from).at(-1)?.value;
  const baseline = before ?? run.startingEquity;
  let peak = baseline;
  const equity = run.dailyEquity.filter(within);
  const drawdown = baseline === null ? [] : equity.map((p) => {
    peak = Math.max(peak ?? p.value, p.value);
    return { date: p.date, value: (p.value / peak - 1) * 100 };
  });
  return {
    cumulative, equity, drawdown, totalR: cumulative.at(-1)?.value ?? 0,
    maxDrawdown: drawdown.length ? Math.min(0, ...drawdown.map((p) => p.value)) : null,
  };
}

/** Deduplicate in-flight work, expire stale data, and bound memory across experiments. */
export function detailCache<T>(load: (id: string) => Promise<T>, maxEntries = 12, ttlMs = 300_000) {
  const entries = new Map<string, { at: number; promise: Promise<T> }>();
  return (id: string): Promise<T> => {
    const old = entries.get(id);
    if (old && Date.now() - old.at < ttlMs) {
      entries.delete(id); entries.set(id, old);
      return old.promise;
    }
    const entry = { at: Date.now(), promise: load(id) };
    entries.delete(id); entries.set(id, entry);
    while (entries.size > maxEntries) entries.delete(entries.keys().next().value!);
    entry.promise.catch(() => { if (entries.get(id) === entry) entries.delete(id); });
    return entry.promise;
  };
}
