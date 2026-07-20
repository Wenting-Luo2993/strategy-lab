export type DashboardDataSource = "fixture" | "supabase";
export type DashboardStatus = "live" | "closed" | "empty" | "unavailable";

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
};

export type OperationalMetric = {
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

export type DashboardData = {
  source: DashboardDataSource;
  status: DashboardStatus;
  generatedAt: string;
  account: Account | null;
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