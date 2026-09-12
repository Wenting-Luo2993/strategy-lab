export type DashboardDataSource = "fixture" | "supabase";
export type DashboardStatus = "live" | "closed" | "empty" | "unavailable";
export type EquityPnlProvenance = "broker" | "local" | "legacy" | "unknown" | (string & {});

export type Account = {
  account_id: string;
  broker: string;
  display_name: string;
  currency: string;
  mode: string;
  updated_at?: string;
};

export type EquitySnapshot = {
  snapshot_id: string;
  account_id: string;
  timestamp: string;
  net_liquidation: number | null;
  cash: number | null;
  buying_power: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  base_currency?: string | null;
  net_liquidation_currency?: string | null;
  cash_currency?: string | null;
  buying_power_currency?: string | null;
  realized_pnl_currency?: string | null;
  unrealized_pnl_currency?: string | null;
  pnl_provenance?: EquityPnlProvenance | null;
  realized_pnl_provenance?: EquityPnlProvenance | null;
  unrealized_pnl_provenance?: EquityPnlProvenance | null;
  pnl_version?: number | null;
  local_realized_pnl?: number | null;
  local_realized_pnl_currency?: string | null;
  granularity?: string;
  period_start?: string | null;
  event_type?: string | null;
  source: string;
};

export type Position = {
  position_id: string;
  account_id: string;
  symbol: string;
  quantity: number;
  side: "long" | "short" | string;
  avg_cost: number | null;
  market_price: number | null;
  unrealized_pnl: number | null;
  instrument_currency?: string | null;
  unrealized_pnl_currency?: string | null;
  updated_at: string;
};

export type OrderEvent = {
  event_id: string;
  account_id: string;
  broker: string;
  broker_order_id: string;
  strategy_order_id?: string | null;
  trade_id?: string | null;
  event_type: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number | null;
  expected_price?: number | null;
  slippage_bps?: number | null;
  latency_ms?: number | null;
  occurred_at: string;
  raw_status?: string | null;
  execution_id?: string | null;
  permanent_order_id?: string | null;
  trade_currency?: string | null;
  commission?: number | null;
  commission_currency?: string | null;
  benchmark_type?: string | null;
  benchmark_price?: number | null;
  slippage_amount?: number | null;
  slippage_version?: number;
  slippage_valid?: boolean;
};

export type PriceBar = {
  symbol: string;
  timeframe: string;
  bar_start: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  provider: string;
  ingestion_time: string;
  is_complete: boolean;
};

export type Trade = {
  trade_id: string;
  account_id: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  entry_time: string;
  exit_price?: number | null;
  exit_time?: string | null;
  status: string;
  pnl?: number | null;
  pnl_pct?: number | null;
  strategy?: string | null;
  exit_reason?: string | null;
  broker_order_id?: string | null;
  pnl_currency?: string | null;
};

export type OperationalMetric = {
  metric_id?: string;
  metric_name: string;
  metric_value: number;
  dimensions: Record<string, string> | null;
  timestamp: string;
};

export type StrategyAnnotation = {
  annotation_id: string;
  account_id: string;
  symbol: string;
  strategy: string;
  trading_day: string;
  annotation_type: string;
  key: string;
  value_json: Record<string, unknown>;
  enabled: boolean;
};

export type PublishStatus = {
  pending: number;
  failed: number;
  publishing: number;
  dead_letter: number;
  published: number;
};

export type StrategyConfigSummary = {
  name: string;
  version: string;
  description: string;
  symbols: string[];
  timeframe: string;
  strategyType: string;
  breakoutEvaluation?: string | null;
  positionSizeMethod: string;
  maxShares?: number | null;
  maxPositionPct?: number | null;
};

export type DashboardData = {
  source: DashboardDataSource;
  status: DashboardStatus;
  generatedAt: string;
  account: Account | null;
  accounts?: Account[];
  equity: EquitySnapshot[];
  positions: Position[];
  orderEvents: OrderEvent[];
  priceBars: PriceBar[];
  trades: Trade[];
  metrics: OperationalMetric[];
  annotations: StrategyAnnotation[];
  publishStatus: PublishStatus;
  error?: string;
};