import assert from "node:assert/strict";
import test from "node:test";

import {
  activePositionsFor,
  aggregatePnlByCurrency,
  closedTradesFor,
  dashboardDataForAccount,
  netLiquidationFor,
  positionUnrealizedPnlCurrencyFor,
  realizedPnlFor,
  realizedPnlPresentationFor,
  unrealizedPnlFor,
  unrealizedPnlPresentationFor,
} from "../src/data/dashboardSelectors.ts";

function emptyData() {
  return {
    source: "fixture",
    status: "live",
    generatedAt: "2026-09-09T12:00:00Z",
    account: null,
    accounts: [],
    equity: [],
    positions: [],
    orderEvents: [],
    priceBars: [],
    trades: [],
    metrics: [],
    annotations: [],
    publishStatus: { pending: 0, failed: 0, publishing: 0, dead_letter: 0, published: 0 },
  };
}

test("keeps a long-lived unchanged open position visible", () => {
  const positions = [{
    position_id: "A:QQQ",
    account_id: "A",
    symbol: "QQQ",
    quantity: 10,
    side: "long",
    avg_cost: 100,
    market_price: 101,
    unrealized_pnl: 10,
    updated_at: "2026-09-01T12:00:00Z",
  }];

  assert.deepEqual(activePositionsFor(positions), positions);
});

test("uses instrument currency for position P&L when an older row has the account currency", () => {
  const position = {
    position_id: "A:QQQ",
    account_id: "A",
    symbol: "QQQ",
    quantity: 6,
    side: "short",
    avg_cost: 744.45,
    market_price: 738.86,
    unrealized_pnl: 33.56,
    instrument_currency: "USD",
    unrealized_pnl_currency: "CAD",
    updated_at: "2026-09-23T17:11:00Z",
  };

  assert.equal(positionUnrealizedPnlCurrencyFor(position), "USD");
});

test("does not infer net liquidation currency from account currency", () => {
  const data = emptyData();
  data.account = {
    account_id: "A",
    broker: "ib",
    display_name: "A",
    currency: "USD",
    mode: "paper",
  };
  data.equity = [{
    snapshot_id: "equity",
    account_id: "A",
    timestamp: data.generatedAt,
    net_liquidation: 1000,
    cash: null,
    buying_power: null,
    realized_pnl: null,
    unrealized_pnl: null,
    source: "ib",
    net_liquidation_currency: null,
  }];

  assert.deepEqual(netLiquidationFor(data), {
    value: 1000,
    currency: null,
  });
});

test("filters all account-owned dashboard surfaces and currencies", () => {
  const data = emptyData();
  data.accounts = [
    { account_id: "A", broker: "ib", display_name: "A", currency: "CAD", mode: "paper" },
    { account_id: "B", broker: "ib", display_name: "B", currency: "USD", mode: "paper" },
  ];
  data.account = data.accounts[0];
  data.equity = [
    { snapshot_id: "ea", account_id: "A", timestamp: "2026-09-09T12:00:00Z", net_liquidation: 1, cash: 1, buying_power: 1, realized_pnl: null, unrealized_pnl: 1, local_realized_pnl: 11, local_realized_pnl_currency: "CAD", source: "ib" },
    { snapshot_id: "eb", account_id: "B", timestamp: "2026-09-09T12:00:00Z", net_liquidation: 2, cash: 2, buying_power: 2, realized_pnl: null, unrealized_pnl: 2, local_realized_pnl: 22, local_realized_pnl_currency: "USD", source: "ib" },
  ];
  data.positions = [
    { position_id: "A:QQQ", account_id: "A", symbol: "QQQ", quantity: 1, side: "long", avg_cost: 1, market_price: 1, unrealized_pnl: 1, updated_at: data.generatedAt },
    { position_id: "B:SPY", account_id: "B", symbol: "SPY", quantity: 1, side: "long", avg_cost: 1, market_price: 1, unrealized_pnl: 2, updated_at: data.generatedAt },
  ];
  data.orderEvents = [
    { event_id: "oa", account_id: "A", broker: "ib", broker_order_id: "1", event_type: "ORDER_SENT", symbol: "QQQ", side: "buy", quantity: 1, price: 1, occurred_at: data.generatedAt },
    { event_id: "ob", account_id: "B", broker: "ib", broker_order_id: "2", event_type: "ORDER_SENT", symbol: "SPY", side: "buy", quantity: 1, price: 1, occurred_at: data.generatedAt },
  ];
  data.trades = [
    { trade_id: "ta", account_id: "A", symbol: "QQQ", side: "buy", quantity: 1, entry_price: 1, entry_time: data.generatedAt, status: "closed", exit_price: 2, exit_time: data.generatedAt, pnl: 11 },
    { trade_id: "tb", account_id: "B", symbol: "SPY", side: "buy", quantity: 1, entry_price: 1, entry_time: data.generatedAt, status: "closed", exit_price: 2, exit_time: data.generatedAt, pnl: 22 },
  ];
  data.metrics = [
    { metric_name: "latency_ms", metric_value: 1, dimensions: { account_id: "A" }, timestamp: data.generatedAt },
    { metric_name: "latency_ms", metric_value: 2, dimensions: { account_id: "B" }, timestamp: data.generatedAt },
  ];
  data.annotations = [
    { annotation_id: "aa", account_id: "A", symbol: "QQQ", strategy: "x", trading_day: "2026-09-09", annotation_type: "x", key: "x", value_json: {}, enabled: true },
    { annotation_id: "ab", account_id: "B", symbol: "SPY", strategy: "x", trading_day: "2026-09-09", annotation_type: "x", key: "x", value_json: {}, enabled: true },
  ];

  const selected = dashboardDataForAccount(data, "B");

  assert.equal(selected.account.currency, "USD");
  for (const collection of [selected.equity, selected.positions, selected.orderEvents, selected.trades, selected.annotations]) {
    assert.ok(collection.every((item) => item.account_id === "B"));
  }
  assert.ok(selected.metrics.every((item) => item.dimensions.account_id === "B"));
  assert.equal(realizedPnlFor(selected), 22);
});

test("uses authoritative partial-fill trade P&L", () => {
  const data = emptyData();
  data.account = { account_id: "A", broker: "ib", display_name: "A", currency: "USD", mode: "paper" };
  data.trades = [{
    trade_id: "partial",
    account_id: "A",
    symbol: "QQQ",
    side: "buy",
    quantity: 3,
    entry_price: 100.5,
    entry_time: "2026-09-09T10:00:00Z",
    exit_price: 102,
    exit_time: "2026-09-09T11:00:00Z",
    status: "closed",
    pnl: 4.5,
  }];
  data.orderEvents = [
    { event_id: "e1", account_id: "A", broker: "ib", broker_order_id: "1", trade_id: "partial", event_type: "ORDER_FILLED", symbol: "QQQ", side: "buy", quantity: 1, price: 100, occurred_at: "2026-09-09T10:00:00Z" },
    { event_id: "e2", account_id: "A", broker: "ib", broker_order_id: "1", trade_id: "partial", event_type: "ORDER_FILLED", symbol: "QQQ", side: "buy", quantity: 2, price: 100.75, occurred_at: "2026-09-09T10:01:00Z" },
  ];

  assert.deepEqual(closedTradesFor(data).map((trade) => [trade.quantity, trade.pnl]), [[3, 4.5]]);
});

test("includes realized P&L from a still-open partially closed trade", () => {
  const data = emptyData();
  data.account = { account_id: "A", broker: "ib", display_name: "A", currency: "USD", mode: "paper" };
  data.equity = [{
    snapshot_id: "partial-equity",
    account_id: "A",
    timestamp: "2026-09-09T11:00:00Z",
    net_liquidation: 1000,
    cash: 500,
    buying_power: 1000,
    realized_pnl: null,
    unrealized_pnl: null,
    local_realized_pnl: 8,
    local_realized_pnl_currency: "USD",
    source: "ib",
  }];
  data.trades = [{
    trade_id: "closed",
    account_id: "A",
    symbol: "SPY",
    side: "buy",
    quantity: 1,
    entry_price: 100,
    entry_time: "2026-09-09T09:00:00Z",
    exit_price: 102,
    exit_time: "2026-09-09T10:00:00Z",
    status: "closed",
    pnl: 2,
  }, {
    trade_id: "partial-open",
    account_id: "A",
    symbol: "QQQ",
    side: "buy",
    quantity: 6,
    closed_quantity: 4,
    entry_price: 100,
    entry_time: "2026-09-09T09:30:00Z",
    exit_price: 101.5,
    exit_time: null,
    status: "open",
    pnl: 6,
  }];

  assert.equal(realizedPnlFor(data), 8);
});

test("does not aggregate fallback P&L across currencies", () => {
  const data = emptyData();
  data.account = { account_id: "A", broker: "ib", display_name: "A", currency: "USD", mode: "paper" };
  data.trades = [
    { trade_id: "usd", account_id: "A", symbol: "QQQ", side: "buy", quantity: 1, entry_price: 1, entry_time: data.generatedAt, exit_price: 2, exit_time: data.generatedAt, status: "closed", pnl: 1, pnl_currency: "USD" },
    { trade_id: "cad", account_id: "A", symbol: "SHOP", side: "buy", quantity: 1, entry_price: 1, entry_time: data.generatedAt, exit_price: 2, exit_time: data.generatedAt, status: "closed", pnl: 1, pnl_currency: "CAD" },
  ];
  const positions = [
    { position_id: "A:QQQ", account_id: "A", symbol: "QQQ", quantity: 1, side: "long", avg_cost: 1, market_price: 2, unrealized_pnl: 1, instrument_currency: "USD", unrealized_pnl_currency: "USD", updated_at: data.generatedAt },
    { position_id: "A:SHOP", account_id: "A", symbol: "SHOP", quantity: 1, side: "long", avg_cost: 1, market_price: 2, unrealized_pnl: 1, instrument_currency: "CAD", unrealized_pnl_currency: "CAD", updated_at: data.generatedAt },
  ];

  assert.equal(realizedPnlFor(data), null);
  assert.equal(unrealizedPnlFor(undefined, positions), null);
  assert.deepEqual(aggregatePnlByCurrency([
    { pnl: 1, currency: "BASE" },
    { pnl: 2, currency: "BASE" },
  ]), { value: null, currency: null });
  assert.deepEqual(aggregatePnlByCurrency([
    { pnl: 1, currency: "usd" },
    { pnl: 2, currency: "USD" },
  ]), { value: 3, currency: "USD" });
});

test("does not infer missing position or trade P&L currencies", () => {
  const data = emptyData();
  data.account = { account_id: "A", broker: "ib", display_name: "A", currency: "USD", mode: "paper" };
  data.trades = [{
    trade_id: "unknown-currency",
    account_id: "A",
    symbol: "QQQ",
    side: "buy",
    quantity: 1,
    entry_price: 100,
    entry_time: "2026-09-09T10:00:00Z",
    exit_price: 101,
    exit_time: "2026-09-09T11:00:00Z",
    status: "closed",
    pnl: 1,
  }];
  const positions = [{
    position_id: "A:QQQ",
    account_id: "A",
    symbol: "QQQ",
    quantity: 1,
    side: "long",
    avg_cost: 100,
    market_price: 101,
    unrealized_pnl: 1,
    instrument_currency: "USD",
    updated_at: data.generatedAt,
  }];

  assert.equal(closedTradesFor(data)[0].currency, null);
  assert.equal(realizedPnlFor(data), null);
  assert.equal(unrealizedPnlFor(undefined, positions), 1);
  assert.deepEqual(
    unrealizedPnlPresentationFor(undefined, positions),
    {
      value: 1,
      currency: "USD",
      provenance: "local",
      version: null,
      label: "Position unrealized P&L",
    },
  );
});

test("labels equity P&L as broker only with broker provenance", () => {
  const data = emptyData();
  data.equity = [{
    snapshot_id: "legacy",
    account_id: "A",
    timestamp: data.generatedAt,
    net_liquidation: 100,
    cash: 50,
    buying_power: 100,
    realized_pnl: 5,
    unrealized_pnl: 6,
    realized_pnl_currency: "USD",
    unrealized_pnl_currency: "USD",
    source: "ib",
  }];

  assert.equal(realizedPnlPresentationFor(data).label, "Realized P&L (source unknown)");
  assert.equal(
    unrealizedPnlPresentationFor(data.equity[0], []).label,
    "Unrealized P&L (source unknown)",
  );

  data.equity[0].pnl_provenance = "broker";
  data.equity[0].pnl_version = 2;
  assert.deepEqual(realizedPnlPresentationFor(data), {
    value: 5,
    currency: "USD",
    provenance: "broker",
    version: 2,
    label: "Broker realized P&L",
  });
  assert.equal(
    unrealizedPnlPresentationFor(data.equity[0], []).label,
    "Broker unrealized P&L",
  );

  data.equity[0].realized_pnl_provenance = "legacy";
  data.equity[0].unrealized_pnl_provenance = "local";
  assert.equal(realizedPnlPresentationFor(data).label, "Legacy realized P&L");
  assert.equal(
    unrealizedPnlPresentationFor(data.equity[0], []).label,
    "Local unrealized P&L (diagnostic)",
  );
});

test("chronologically consumes legacy entry quantities without trade IDs", () => {
  const data = emptyData();
  data.orderEvents = [
    { event_id: "a-entry-1", account_id: "A", broker: "ib", broker_order_id: "a1", event_type: "ORDER_FILLED", symbol: "QQQ", side: "buy", quantity: 3, price: 100, trade_currency: "USD", occurred_at: "2026-09-09T10:00:00Z" },
    { event_id: "b-entry", account_id: "B", broker: "ib", broker_order_id: "a2", event_type: "ORDER_FILLED", symbol: "QQQ", side: "buy", quantity: 1, price: 50, trade_currency: "USD", occurred_at: "2026-09-09T10:01:00Z" },
    { event_id: "a-close-1", account_id: "A", broker: "ib", broker_order_id: "a2", event_type: "TRADE_CLOSED", symbol: "QQQ", side: "sell", quantity: 1, price: 110, trade_currency: "USD", occurred_at: "2026-09-09T10:02:00Z" },
    { event_id: "a-close-2", account_id: "A", broker: "ib", broker_order_id: "a3", event_type: "TRADE_CLOSED", symbol: "QQQ", side: "sell", quantity: 2, price: 120, trade_currency: "USD", occurred_at: "2026-09-09T10:03:00Z" },
    { event_id: "a-entry-2", account_id: "A", broker: "ib", broker_order_id: "a4", event_type: "ORDER_FILLED", symbol: "QQQ", side: "buy", quantity: 1, price: 200, trade_currency: "USD", occurred_at: "2026-09-09T10:04:00Z" },
    { event_id: "a-close-3", account_id: "A", broker: "ib", broker_order_id: "a5", event_type: "TRADE_CLOSED", symbol: "QQQ", side: "sell", quantity: 1, price: 210, trade_currency: "USD", occurred_at: "2026-09-09T10:05:00Z" },
    { event_id: "b-close", account_id: "B", broker: "ib", broker_order_id: "b2", event_type: "TRADE_CLOSED", symbol: "QQQ", side: "sell", quantity: 1, price: 55, trade_currency: "USD", occurred_at: "2026-09-09T10:06:00Z" },
  ];

  const byId = new Map(closedTradesFor(data).map((trade) => [trade.id, trade]));
  assert.deepEqual(
    ["a-close-1", "a-close-2", "a-close-3", "b-close"].map((id) => [
      id,
      byId.get(id).entryPrice,
      byId.get(id).pnl,
    ]),
    [
      ["a-close-1", 100, 10],
      ["a-close-2", 100, 40],
      ["a-close-3", 200, 10],
      ["b-close", 50, 5],
    ],
  );
});

test("keeps reconstructed legacy currency unknown when any consumed event is unknown", () => {
  const data = emptyData();
  data.account = { account_id: "A", broker: "ib", display_name: "A", currency: "USD", mode: "paper" };
  data.orderEvents = [
    { event_id: "entry", account_id: "A", broker: "ib", broker_order_id: "1", event_type: "ORDER_FILLED", symbol: "QQQ", side: "buy", quantity: 1, price: 100, trade_currency: "USD", occurred_at: "2026-09-09T10:00:00Z" },
    { event_id: "close", account_id: "A", broker: "ib", broker_order_id: "2", event_type: "TRADE_CLOSED", symbol: "QQQ", side: "sell", quantity: 1, price: 101, occurred_at: "2026-09-09T11:00:00Z" },
  ];

  assert.equal(closedTradesFor(data)[0].currency, null);
  assert.equal(realizedPnlFor(data), null);
});
