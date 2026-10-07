import assert from "node:assert/strict";
import test from "node:test";

import {
  equityCurvePoints,
  performanceSummary,
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

test("assigns unique ordered timestamps when trades share an exit time", () => {
  const exitTime = "2026-09-18T15:00:00Z";
  const points = tradePnlPoints([
    { id: "a", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 100, exitPrice: 101, exitTime, pnl: 1, currency: "USD" },
    { id: "b", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 100, exitPrice: 102, exitTime, pnl: 2, currency: "USD" },
    { id: "c", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 100, exitPrice: 103, exitTime, pnl: 3, currency: "USD" },
  ]);

  assert.deepEqual(
    points.map((point) => point.time),
    [points[0].time, points[0].time + 1, points[0].time + 2],
  );
});

test("summarizes drawdown, losing streaks, and MAR from full history", () => {
  const equity = [
    { timestamp: "2025-01-01T20:00:00Z", net_liquidation: 100, account_id: "A", snapshot_id: "1", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
    { timestamp: "2025-07-02T20:00:00Z", net_liquidation: 120, account_id: "A", snapshot_id: "2", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
    { timestamp: "2025-10-01T20:00:00Z", net_liquidation: 90, account_id: "A", snapshot_id: "3", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
    { timestamp: "2026-01-01T20:00:00Z", net_liquidation: 110, account_id: "A", snapshot_id: "4", cash: null, buying_power: null, realized_pnl: null, unrealized_pnl: null, source: "ib" },
  ];
  const trades = [
    { id: "latest-loss", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 1, exitPrice: 0, exitTime: "2025-01-05T20:00:00Z", pnl: -1, currency: "USD" },
    { id: "first-loss", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 1, exitPrice: 0, exitTime: "2025-01-01T20:00:00Z", pnl: -1, currency: "USD" },
    { id: "win", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 1, exitPrice: 2, exitTime: "2025-01-03T20:00:00Z", pnl: 1, currency: "USD" },
    { id: "middle-loss", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 1, exitPrice: 0, exitTime: "2025-01-02T20:00:00Z", pnl: -1, currency: "USD" },
    { id: "current-loss", symbol: "QQQ", side: "long", quantity: 1, entryPrice: 1, exitPrice: 0, exitTime: "2025-01-04T20:00:00Z", pnl: -1, currency: "USD" },
  ];

  const summary = performanceSummary(equity, trades);

  assert.equal(summary.maximumDrawdownPct, 25);
  assert.equal(summary.longestLosingStreak, 2);
  assert.equal(summary.currentLosingStreak, 2);
  assert.ok(summary.marRatio > 0.39 && summary.marRatio < 0.41);
});

test("returns unavailable risk ratios without enough equity history", () => {
  const summary = performanceSummary([], []);

  assert.deepEqual(summary, {
    maximumDrawdownPct: null,
    longestLosingStreak: 0,
    currentLosingStreak: 0,
    marRatio: null,
  });
});
