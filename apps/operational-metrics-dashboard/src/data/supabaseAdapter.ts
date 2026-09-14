import type {
  Account,
  DashboardData,
  EquityPnlProvenance,
  EquitySnapshot,
  OperationalMetric,
  OrderEvent,
  Position,
  PriceBar,
  StrategyAnnotation,
  Trade,
} from "./types";

type SupabaseEquitySnapshot = EquitySnapshot & {
  pnl_source?: EquityPnlProvenance | null;
  realized_pnl_source?: EquityPnlProvenance | null;
  unrealized_pnl_source?: EquityPnlProvenance | null;
  pnl_provenance_version?: number | null;
};

type TableMap = {
  accounts: Account;
  equity_snapshots: SupabaseEquitySnapshot;
  positions: Position;
  order_events: OrderEvent;
  price_bars: PriceBar;
  trades: Trade;
  operational_metrics: OperationalMetric;
  strategy_annotations: StrategyAnnotation;
};

const supabasePageSize = 1000;

export async function getSupabaseDashboardData(): Promise<DashboardData> {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !supabaseAnonKey) {
    return unavailableDashboardData("Supabase read endpoint is not configured.");
  }

  try {
    const accounts = await queryTable(
      "accounts",
      supabaseUrl,
      supabaseAnonKey,
      "select=*&order=account_id&limit=100",
    );
    const [priceBars, rowsByAccount] = await Promise.all([
      queryTable("price_bars", supabaseUrl, supabaseAnonKey, "select=*&order=bar_start.desc&limit=2000"),
      Promise.all(accounts.map(async (account) => {
        const accountId = encodeURIComponent(account.account_id);
        const metricsQuery = accounts.length === 1
          ? "select=*&order=timestamp.desc&limit=100"
          : `select=*&dimensions->>account_id=eq.${accountId}&order=timestamp.desc&limit=100`;
        return Promise.all([
          queryTable("equity_snapshots", supabaseUrl, supabaseAnonKey, `select=*&account_id=eq.${accountId}&order=timestamp.desc&limit=50`),
          queryTable("positions", supabaseUrl, supabaseAnonKey, `select=*&account_id=eq.${accountId}&order=updated_at.desc&limit=50`),
          queryTable("order_events", supabaseUrl, supabaseAnonKey, `select=*&account_id=eq.${accountId}&order=occurred_at.desc&limit=100`),
          queryAllTableRows(
            "trades",
            supabaseUrl,
            supabaseAnonKey,
            `select=*&account_id=eq.${accountId}&order=entry_time.desc,trade_id.asc`,
          ),
          queryTable("operational_metrics", supabaseUrl, supabaseAnonKey, metricsQuery),
          queryTable("strategy_annotations", supabaseUrl, supabaseAnonKey, `select=*&account_id=eq.${accountId}&enabled=eq.true&limit=100`),
        ]);
      })),
    ]);
    const equity = rowsByAccount
      .flatMap((rows) => rows[0] as SupabaseEquitySnapshot[])
      .map(adaptEquitySnapshot);
    const positions = rowsByAccount.flatMap((rows) => rows[1] as Position[]);
    const orderEvents = rowsByAccount.flatMap((rows) => rows[2] as OrderEvent[]);
    const trades = rowsByAccount.flatMap((rows) => rows[3] as Trade[]);
    const metrics = rowsByAccount.flatMap((rows) => rows[4] as OperationalMetric[]);
    const annotations = rowsByAccount.flatMap((rows) => rows[5] as StrategyAnnotation[]);

    return {
      source: "supabase",
      status: priceBars.length || orderEvents.length || equity.length ? "live" : "empty",
      generatedAt: new Date().toISOString(),
      account: accounts[0] ?? null,
      accounts,
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

export function adaptEquitySnapshot(row: SupabaseEquitySnapshot): EquitySnapshot {
  const sharedProvenance = normalizePnlProvenance(
    row.pnl_provenance ?? row.pnl_source,
  );
  return {
    ...row,
    pnl_provenance: sharedProvenance,
    realized_pnl_provenance: normalizePnlProvenance(
      row.realized_pnl_provenance
      ?? row.realized_pnl_source
      ?? sharedProvenance,
    ),
    unrealized_pnl_provenance: normalizePnlProvenance(
      row.unrealized_pnl_provenance
      ?? row.unrealized_pnl_source
      ?? sharedProvenance,
    ),
    pnl_version: normalizePnlVersion(
      row.pnl_version ?? row.pnl_provenance_version,
    ),
  };
}

function normalizePnlProvenance(
  provenance: EquityPnlProvenance | null | undefined,
): EquityPnlProvenance | null {
  const normalized = provenance?.trim().toLowerCase();
  return normalized || null;
}

function normalizePnlVersion(version: number | null | undefined): number | null {
  return Number.isInteger(version) && Number(version) >= 1 ? Number(version) : null;
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

export async function queryAllTableRows<TableName extends keyof TableMap>(
  table: TableName,
  supabaseUrl: string,
  supabaseAnonKey: string,
  query: string,
  pageSize = supabasePageSize,
): Promise<TableMap[TableName][]> {
  const rows: TableMap[TableName][] = [];
  for (let offset = 0; ; offset += pageSize) {
    const endpoint = `${supabaseUrl.replace(/\/$/, "")}/rest/v1/${table}?${query}`;
    const response = await fetch(endpoint, {
      headers: {
        apikey: supabaseAnonKey,
        Authorization: `Bearer ${supabaseAnonKey}`,
        Range: `${offset}-${offset + pageSize - 1}`,
        "Range-Unit": "items",
      },
    });
    if (!response.ok) {
      throw new Error(`Supabase ${table} query failed: ${response.status}`);
    }
    const page = await response.json() as TableMap[TableName][];
    rows.push(...page);
    if (page.length < pageSize) {
      return rows;
    }
  }
}