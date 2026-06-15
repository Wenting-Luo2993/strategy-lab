export type OperationalMetric = {
  id?: number;
  metric_name: string;
  metric_value: number;
  dimensions: Record<string, string> | null;
  timestamp: string;
  created_at?: string;
};

export type MetricSummary = {
  count: number;
  avgLatencyMs: number | null;
  avgSlippageBps: number | null;
  maxLatencyMs: number | null;
  worstSlippageBps: number | null;
  latestTimestamp: string | null;
  fills: number;
};

export type SymbolMetric = {
  symbol: string;
  fills: number;
  avgLatencyMs: number | null;
  avgSlippageBps: number | null;
};

const TABLE = process.env.SUPABASE_OPERATIONAL_METRICS_TABLE ?? "operational_metrics";

export async function fetchOperationalMetrics(): Promise<OperationalMetric[]> {
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseAnonKey = process.env.SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseAnonKey) {
    return [];
  }

  const endpoint = new URL(`/rest/v1/${TABLE}`, supabaseUrl);
  endpoint.searchParams.set("select", "*");
  endpoint.searchParams.set("order", "timestamp.desc");
  endpoint.searchParams.set("limit", "500");

  const response = await fetch(endpoint, {
    headers: {
      apikey: supabaseAnonKey,
      Authorization: `Bearer ${supabaseAnonKey}`,
    },
    next: { revalidate: 30 },
  });

  if (!response.ok) {
    throw new Error(`Supabase metrics query failed: ${response.status}`);
  }

  return response.json();
}

export function summarizeMetrics(metrics: OperationalMetric[]): MetricSummary {
  const latency = valuesFor(metrics, "latency_ms");
  const slippage = valuesFor(metrics, "slippage_bps");
  const fillQuantity = valuesFor(metrics, "fill_quantity");

  return {
    count: metrics.length,
    avgLatencyMs: average(latency),
    avgSlippageBps: average(slippage),
    maxLatencyMs: latency.length ? Math.max(...latency) : null,
    worstSlippageBps: slippage.length ? Math.max(...slippage) : null,
    latestTimestamp: metrics[0]?.timestamp ?? null,
    fills: fillQuantity.length,
  };
}

export function summarizeBySymbol(metrics: OperationalMetric[]): SymbolMetric[] {
  const symbols = new Map<string, OperationalMetric[]>();

  for (const metric of metrics) {
    const symbol = metric.dimensions?.symbol ?? "UNKNOWN";
    if (!symbols.has(symbol)) {
      symbols.set(symbol, []);
    }
    symbols.get(symbol)?.push(metric);
  }

  return [...symbols.entries()]
    .map(([symbol, rows]) => ({
      symbol,
      fills: valuesFor(rows, "fill_quantity").length,
      avgLatencyMs: average(valuesFor(rows, "latency_ms")),
      avgSlippageBps: average(valuesFor(rows, "slippage_bps")),
    }))
    .sort((left, right) => right.fills - left.fills);
}

export function latestFillGroups(metrics: OperationalMetric[]) {
  const groups = new Map<string, OperationalMetric[]>();

  for (const metric of metrics) {
    const orderId = metric.dimensions?.broker_order_id ?? "UNKNOWN";
    if (!groups.has(orderId)) {
      groups.set(orderId, []);
    }
    groups.get(orderId)?.push(metric);
  }

  return [...groups.entries()]
    .map(([orderId, rows]) => {
      const first = rows[0];
      return {
        orderId,
        symbol: first?.dimensions?.symbol ?? "UNKNOWN",
        side: first?.dimensions?.side ?? "unknown",
        status: first?.dimensions?.status ?? "unknown",
        timestamp: rows.map((row) => row.timestamp).sort().at(-1) ?? null,
        expected: latestValue(rows, "expected_fill_price"),
        actual: latestValue(rows, "actual_fill_price"),
        slippageBps: latestValue(rows, "slippage_bps"),
        latencyMs: latestValue(rows, "latency_ms"),
        quantity: latestValue(rows, "fill_quantity"),
      };
    })
    .sort((left, right) => (right.timestamp ?? "").localeCompare(left.timestamp ?? ""))
    .slice(0, 25);
}

function valuesFor(metrics: OperationalMetric[], name: string): number[] {
  return metrics
    .filter((metric) => metric.metric_name === name)
    .map((metric) => metric.metric_value)
    .filter((value) => Number.isFinite(value));
}

function average(values: number[]): number | null {
  if (!values.length) {
    return null;
  }
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function latestValue(metrics: OperationalMetric[], name: string): number | null {
  const metric = metrics.find((row) => row.metric_name === name);
  return metric?.metric_value ?? null;
}

export function formatNumber(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) {
    return "--";
  }
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function formatTimestamp(value: string | null): string {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}