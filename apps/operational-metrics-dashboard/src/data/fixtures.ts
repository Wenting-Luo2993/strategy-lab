import type { DashboardData, DashboardStatus, PriceBar } from "./types";

const baseDay = "2026-07-20";

function bars(symbol: string, start: number, drift: number): PriceBar[] {
  return Array.from({ length: 18 }, (_, index) => {
    const open = start + index * drift + Math.sin(index / 2) * 0.42;
    const close = open + Math.cos(index / 2.5) * 0.58;
    const high = Math.max(open, close) + 0.72;
    const low = Math.min(open, close) - 0.64;
    const minute = 30 + index * 5;
    const hour = 9 + Math.floor(minute / 60);
    const normalizedMinute = minute % 60;
    const timestamp = `${baseDay}T${String(hour).padStart(2, "0")}:${String(normalizedMinute).padStart(2, "0")}:00-04:00`;
    return {
      symbol,
      timeframe: "5m",
      bar_start: timestamp,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: 750000 + index * 27000,
      provider: "polygon",
      ingestion_time: timestamp,
      is_complete: true,
    };
  });
}

export const liveMarketFixture: DashboardData = {
  source: "fixture",
  status: "live",
  generatedAt: `${baseDay}T10:56:00-04:00`,
  account: {
    account_id: "IB-PAPER-001",
    broker: "interactive_brokers",
    display_name: "IB Paper",
    currency: "USD",
    mode: "paper",
    updated_at: `${baseDay}T10:56:00-04:00`,
  },
  equity: [
    {
      snapshot_id: "snap-1056",
      account_id: "IB-PAPER-001",
      timestamp: `${baseDay}T10:56:00-04:00`,
      net_liquidation: 102438.21,
      cash: 84231.48,
      buying_power: 321884.9,
      realized_pnl: 218.44,
      unrealized_pnl: 73.92,
      source: "interactive_brokers",
    },
  ],
  positions: [
    {
      position_id: "IB-PAPER-001:QQQ",
      account_id: "IB-PAPER-001",
      symbol: "QQQ",
      quantity: 42,
      side: "long",
      avg_cost: 497.28,
      market_price: 499.04,
      unrealized_pnl: 73.92,
      updated_at: `${baseDay}T10:56:00-04:00`,
    },
  ],
  orderEvents: [
    {
      event_id: "ORDER_FILLED:1183",
      account_id: "IB-PAPER-001",
      broker: "interactive_brokers",
      broker_order_id: "1183",
      strategy_order_id: "orb-qqq-01",
      trade_id: "trade-qqq-20260720-01",
      event_type: "ORDER_FILLED",
      symbol: "QQQ",
      side: "buy",
      quantity: 42,
      price: 497.28,
      expected_price: 497.21,
      slippage_bps: 1.41,
      latency_ms: 184,
      occurred_at: `${baseDay}T09:48:17-04:00`,
      raw_status: "Filled",
    },
    {
      event_id: "ORDER_SENT:1183",
      account_id: "IB-PAPER-001",
      broker: "interactive_brokers",
      broker_order_id: "1183",
      strategy_order_id: "orb-qqq-01",
      trade_id: "trade-qqq-20260720-01",
      event_type: "ORDER_SENT",
      symbol: "QQQ",
      side: "buy",
      quantity: 42,
      price: 497.21,
      expected_price: 497.21,
      occurred_at: `${baseDay}T09:48:16-04:00`,
      raw_status: "Submitted",
    },
  ],
  priceBars: [...bars("QQQ", 496.4, 0.18), ...bars("SPY", 623.1, -0.03)],
  trades: [
    {
      trade_id: "trade-qqq-20260720-01",
      account_id: "IB-PAPER-001",
      symbol: "QQQ",
      side: "buy",
      quantity: 42,
      entry_price: 497.28,
      entry_time: `${baseDay}T09:48:17-04:00`,
      status: "open",
      pnl: 73.92,
      pnl_pct: 0.35,
      strategy: "ORB Conservative",
      broker_order_id: "1183",
    },
    {
      trade_id: "trade-spy-20260720-01",
      account_id: "IB-PAPER-001",
      symbol: "SPY",
      side: "sell",
      quantity: 30,
      entry_price: 623.42,
      entry_time: `${baseDay}T09:42:11-04:00`,
      exit_price: 622.66,
      exit_time: `${baseDay}T10:18:03-04:00`,
      status: "closed",
      pnl: 22.8,
      pnl_pct: 0.12,
      strategy: "ORB Conservative",
      exit_reason: "take_profit",
      broker_order_id: "1179",
    },
  ],
  metrics: [
    { metric_name: "latency_ms", metric_value: 184, dimensions: { symbol: "QQQ", broker_order_id: "1183" }, timestamp: `${baseDay}T09:48:17-04:00` },
    { metric_name: "slippage_bps", metric_value: 1.41, dimensions: { symbol: "QQQ", broker_order_id: "1183" }, timestamp: `${baseDay}T09:48:17-04:00` },
    { metric_name: "latency_ms", metric_value: 232, dimensions: { symbol: "SPY", broker_order_id: "1179" }, timestamp: `${baseDay}T10:18:03-04:00` },
    { metric_name: "slippage_bps", metric_value: -0.82, dimensions: { symbol: "SPY", broker_order_id: "1179" }, timestamp: `${baseDay}T10:18:03-04:00` },
  ],
  annotations: [
    {
      annotation_id: "orb-qqq-20260720",
      account_id: "IB-PAPER-001",
      symbol: "QQQ",
      strategy: "ORB Conservative",
      trading_day: baseDay,
      annotation_type: "orb_levels",
      key: "opening_range",
      value_json: { high: 497.21, low: 495.88, range: 1.33 },
      enabled: true,
    },
  ],
  publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 128 },
};

export const closedMarketFixture: DashboardData = {
  ...liveMarketFixture,
  status: "closed",
  generatedAt: `${baseDay}T16:07:00-04:00`,
  positions: [],
  publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 241 },
};

export const emptyFixture: DashboardData = {
  ...liveMarketFixture,
  status: "empty",
  generatedAt: `${baseDay}T08:10:00-04:00`,
  equity: [],
  positions: [],
  orderEvents: [],
  priceBars: [],
  trades: [],
  metrics: [],
  annotations: [],
  publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 0 },
};

export const remoteUnavailableFixture: DashboardData = {
  ...liveMarketFixture,
  status: "unavailable",
  generatedAt: `${baseDay}T11:03:00-04:00`,
  publishStatus: { pending: 4, failed: 2, publishing: 0, dead_letter: 0, published: 128 },
  error: "Supabase read endpoint unavailable; showing last bundled fixture.",
};

export function fixtureByName(name: string | undefined): DashboardData {
  const fixtures: Record<string, DashboardData> = {
    live: liveMarketFixture,
    closed: closedMarketFixture,
    empty: emptyFixture,
    unavailable: remoteUnavailableFixture,
  };
  return fixtures[name ?? "live"] ?? liveMarketFixture;
}

export function fixtureByStatus(status: DashboardStatus): DashboardData {
  return fixtureByName(status);
}