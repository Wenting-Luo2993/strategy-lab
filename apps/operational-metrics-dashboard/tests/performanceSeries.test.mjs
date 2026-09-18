import assert from "node:assert/strict";
import test from "node:test";

import {
  equityCurvePoints,
  tradePnlPoints,
} from "../src/data/performanceSeries.ts";

test("keeps the latest equity snapshot for each market day across full history", () => {
  const points = equityCurvePoints([
    { timestamp: "2026-09-16T19:55:00Z", net_liquidation: 103, account_id: "A", snapshot_id: "4", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
    { timestamp: "2026-09-15T19:55:00Z", net_liquidation: 102, account_id: "A", snapshot_id: "3", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
    { timestamp: "2026-09-15T14:00:00Z", net_liquidation: 100, account_id: "A", snapshot_id: "1", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
    { timestamp: "2026-09-15T15:00:00Z", net_liquidation: 101, account_id: "A", snapshot_id: "2", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
  ]);

  assert.deepEqual(points.map((point) => point.value), [102, 103]);
  assert.ok(points[0].time < points[1].time);
});

test("keeps multiple trades on the same date at their distinct exit times", () => {
  const points = tradePnlPoints([
    { id: "later", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 100, exitPrice: 99, exitTime: "2026-09-15T15:00:00Z", pnl: -1, currency: "USD" },
    { id: "earlier", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 100, exitPrice: 102, exitTime: "2026-09-15T14:00:00Z", pnl: 2, currency: "USD" },
  ]);

  assert.deepEqual(points.map((point) => point.value), [2, -1]);
  assert.ok(points[0].time < points[1].time);
});

test("includes every closed trade in the P&L series", () => {
  const trades = Array.from({ length: 32 }, (_, index) => ({
    id: `trade-${index}`,
    symbol: "QQQ",
    side: "long",
    quantity: 1,
    entryPrice: 100,
    exitPrice: 101,
    exitTime: new Date(Date.UTC(2026, 7, 1 + index, 15)).toISOString(),
    pnl: index - 16,
    currency: "USD",
  }));

  assert.equal(tradePnlPoints(trades).length, 32);
});
