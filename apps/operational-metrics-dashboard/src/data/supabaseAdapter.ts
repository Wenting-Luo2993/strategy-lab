import type {
  Account,
  DashboardData,
  EquitySnapshot,
  OperationalMetric,
  OrderEvent,
  Position,
  PriceBar,
  StrategyAnnotation,
  Trade,
} from "./types";

type TableMap = {
  accounts: Account;
  equity_snapshots: EquitySnapshot;
  positions: Position;
  order_events: OrderEvent;
  price_bars: PriceBar;
  trades: Trade;
  operational_metrics: OperationalMetric;
  strategy_annotations: StrategyAnnotation;
};

export async function getSupabaseDashboardData(): Promise<DashboardData> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !supabaseAnonKey) {
    return unavailableDashboardData("Supabase read endpoint is not configured.");
  }

  try {
    const [accounts, equity, positions, orderEvents, priceBars, trades, metrics, annotations] = await Promise.all([
      queryTable("accounts", supabaseUrl, supabaseAnonKey, "select=*&limit=1"),
      queryTable("equity_snapshots", supabaseUrl, supabaseAnonKey, "select=*&order=timestamp.desc&limit=50"),
      queryTable("positions", supabaseUrl, supabaseAnonKey, "select=*&order=updated_at.desc&limit=50"),
      queryTable("order_events", supabaseUrl, supabaseAnonKey, "select=*&order=occurred_at.desc&limit=50"),
      queryTable("price_bars", supabaseUrl, supabaseAnonKey, "select=*&order=bar_start.desc&limit=2000"),
      queryTable("trades", supabaseUrl, supabaseAnonKey, "select=*&order=entry_time.desc&limit=100"),
      queryTable("operational_metrics", supabaseUrl, supabaseAnonKey, "select=*&order=timestamp.desc&limit=100"),
      queryTable("strategy_annotations", supabaseUrl, supabaseAnonKey, "select=*&enabled=eq.true&limit=100"),
    ]);

    return {
      source: "supabase",
      status: priceBars.length || orderEvents.length || equity.length ? "live" : "empty",
      generatedAt: new Date().toISOString(),
      account: accounts[0] ?? null,
      equity,
      positions,
      orderEvents,
      priceBars: priceBars.sort((left, right) => new Date(left.bar_start).getTime() - new Date(right.bar_start).getTime()),
      trades,
      metrics,
      annotations,
      publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 0 },
    };
  } catch (error) {
    return unavailableDashboardData(error instanceof Error ? error.message : "Supabase dashboard query failed.");
  }
}

function unavailableDashboardData(error: string): DashboardData {
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

async function queryTable<TableName extends keyof TableMap>(
  table: TableName,
  supabaseUrl: string,
  supabaseAnonKey: string,
  query: string,
): Promise<TableMap[TableName][]> {
  const endpoint = `${supabaseUrl.replace(/\/$/, "")}/rest/v1/${table}?${query}`;
  const response = await fetch(endpoint, {
    headers: {
      apikey: supabaseAnonKey,
      Authorization: `Bearer ${supabaseAnonKey}`,
    },
  });
  if (!response.ok) {
    throw new Error(`Supabase ${table} query failed: ${response.status}`);
  }
  return response.json();
}