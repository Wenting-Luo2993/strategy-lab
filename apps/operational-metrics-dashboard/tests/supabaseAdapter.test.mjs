import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptEquitySnapshot,
  queryAllTableRows,
  strategyAnnotationsQuery,
} from "../src/data/supabaseAdapter.ts";

function equityRow(overrides = {}) {
  return {
    snapshot_id: "snapshot",
    account_id: "A",
    timestamp: "2026-09-09T12:00:00Z",
    net_liquidation: 100,
    cash: 50,
    buying_power: 100,
    realized_pnl: 1,
    unrealized_pnl: 2,
    source: "ib",
    ...overrides,
  };
}

test("adapts shared P&L provenance and version fields", () => {
  const adapted = adaptEquitySnapshot(equityRow({
    pnl_provenance: " BROKER ",
    pnl_version: 2,
  }));

  assert.equal(adapted.pnl_provenance, "broker");
  assert.equal(adapted.realized_pnl_provenance, "broker");
  assert.equal(adapted.unrealized_pnl_provenance, "broker");
  assert.equal(adapted.pnl_version, 2);
});

test("preserves field provenance and accepts backward-compatible aliases", () => {
  const adapted = adaptEquitySnapshot(equityRow({
    pnl_source: "legacy",
    realized_pnl_source: "local",
    unrealized_pnl_source: "broker",
    pnl_provenance_version: 3,
  }));

  assert.equal(adapted.pnl_provenance, "legacy");
  assert.equal(adapted.realized_pnl_provenance, "local");
  assert.equal(adapted.unrealized_pnl_provenance, "broker");
  assert.equal(adapted.pnl_version, 3);
});

test("keeps historical rows without valid provenance or version unknown", () => {
  const adapted = adaptEquitySnapshot(equityRow({
    pnl_provenance: " ",
    pnl_version: 0,
  }));

  assert.equal(adapted.pnl_provenance, null);
  assert.equal(adapted.realized_pnl_provenance, null);
  assert.equal(adapted.unrealized_pnl_provenance, null);
  assert.equal(adapted.pnl_version, null);
});

test("retrieves every Supabase page for complete trade history", async (context) => {
  const originalFetch = globalThis.fetch;
  const ranges = [];
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (_url, options) => {
    ranges.push(options.headers.Range);
    const start = Number(options.headers.Range.split("-")[0]);
    const rows = start === 0
      ? [
        { trade_id: "t3", entry_time: "2026-09-03T00:00:00Z" },
        { trade_id: "t2", entry_time: "2026-09-02T00:00:00Z" },
      ]
      : [{ trade_id: "t1", entry_time: "2026-09-01T00:00:00Z" }];
    return new Response(JSON.stringify(rows), { status: 200 });
  };

  const rows = await queryAllTableRows(
    "trades",
    "https://example.supabase.co",
    "anon-key",
    "select=*&account_id=eq.A&order=entry_time.desc,trade_id.asc",
    2,
  );

  assert.deepEqual(rows.map((row) => row.trade_id), ["t3", "t2", "t1"]);
  assert.deepEqual(ranges, ["0-1", "2-3"]);
});

test("loads enabled annotations newest day first without a row cap", () => {
  assert.equal(
    strategyAnnotationsQuery("DU123"),
    "select=*&account_id=eq.DU123&enabled=eq.true&order=trading_day.desc,annotation_id.asc",
  );
});
