import { remoteUnavailableFixture } from "./fixtures";
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
    return remoteUnavailableFixture;
  }

  try {
    const [accounts, equity, positions, orderEvents, priceBars, trades, metrics, annotations] = await Promise.all([
      queryTable("accounts", supabaseUrl, supabaseAnonKey, "select=*&limit=1"),
      queryTable("equity_snapshots", supabaseUrl, supabaseAnonKey, "select=*&order=timestamp.desc&limit=50"),
      queryTable("positions", supabaseUrl, supabaseAnonKey, "select=*&order=updated_at.desc&limit=50"),
      queryTable("order_events", supabaseUrl, supabaseAnonKey, "select=*&order=occurred_at.desc&limit=50"),
      queryTable("price_bars", supabaseUrl, supabaseAnonKey, "select=*&order=bar_start.asc&limit=500"),
      queryTable("trades", supabaseUrl, supabaseAnonKey, "select=*&order=entry_time.desc&limit=100"),
      queryTable("operational_metrics", supabaseUrl, supabaseAnonKey, "select=*&order=timestamp.desc&limit=100"),
      queryTable("strategy_annotations", supabaseUrl, supabaseAnonKey, "select=*&eq.enabled=true&limit=100"),
    ]);

    return {
      source: "supabase",
      status: priceBars.length || orderEvents.length || equity.length ? "live" : "empty",
      generatedAt: new Date().toISOString(),
      account: accounts[0] ?? null,
      equity,
      positions,
      orderEvents,
      priceBars,
      trades,
      metrics,
      annotations,
      publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 0 },
    };
  } catch (error) {
    return {
      ...remoteUnavailableFixture,
      error: error instanceof Error ? error.message : "Supabase dashboard query failed.",
    };
  }
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