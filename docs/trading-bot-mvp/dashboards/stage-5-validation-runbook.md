# Stage 5 Live Dashboard Validation Runbook

**Last Updated:** 2026-07-20  
**Related Tracker:** [live-dashboard-execution-checklist.md](live-dashboard-execution-checklist.md)  
**Supabase Setup SQL:** [supabase-read-model.sql](supabase-read-model.sql)

Use this runbook after the Supabase project is created and the bot environment has dashboard publication enabled. The goal is to prove that real IB paper-trading data flows from the bot into local SQLite, through the publish outbox, into Supabase, and finally into the static dashboard.

---

## 1. Supabase Setup

1. Create or open the Supabase project.
2. Run [supabase-read-model.sql](supabase-read-model.sql) in the Supabase SQL editor with a privileged role.
3. Confirm the browser dashboard has only the anon key:

```bash
NEXT_PUBLIC_DASHBOARD_DATA_SOURCE=supabase
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

4. Confirm the trading bot process uses the service-role key only server-side:

```bash
DASHBOARD__ENABLED=true
DASHBOARD__REMOTE_PROVIDER=supabase
DASHBOARD__SUPABASE_URL=https://your-project.supabase.co
DASHBOARD__SUPABASE_SERVICE_KEY=your-service-role-key
DASHBOARD__ACCOUNT_ID=IB-PAPER-001
```

Do not put the service-role key in `NEXT_PUBLIC_*` variables, static dashboard config, GitHub Pages secrets visible to builds, or browser bundles.

---

## 2. IB Paper Smoke

Run a readonly smoke first:

```bash
python scripts/ib_paper_smoke.py --symbols QQQ,SPY
```

Then run a minimal paper order only when TWS/IB Gateway is connected to the paper account:

```bash
python scripts/ib_paper_smoke.py --symbols QQQ,SPY --order-symbol QQQ --quantity 1 --submit-order --cancel-on-timeout
```

For full dashboard persistence, run the trading bot with dashboard settings enabled so the orchestrator writes price bars, order events, trades, account/equity/position snapshots, metrics, and outbox events.

---

## 3. Local Validation

After the bot has run long enough to complete at least one dashboard timeframe bar, run:

```bash
python scripts/validate_live_dashboard_stage5.py --symbols QQQ,SPY --timeframe 5m
```

After submitting a paper order, require the order/trade/metric path:

```bash
python scripts/validate_live_dashboard_stage5.py --symbols QQQ,SPY --timeframe 5m --require-order
```

The validator is read-only. It checks:

- `./data/market_data.db` has completed `price_bars` per configured symbol/timeframe.
- `./data/dashboard.db` has account, equity, position, and order event rows.
- `./data/trades.db` has dashboard-linked trade rows.
- `./data/local/operational_metrics.db` has fill-quality metrics.
- `./data/local/publish_outbox.db` has publish rows and no unresolved `pending`, `failed`, `publishing`, or `dead_letter` rows after publication/cooldown.

---

## 4. Supabase Validation

Once publication is enabled and credentials are present, run:

```bash
python scripts/validate_live_dashboard_stage5.py --symbols QQQ,SPY --timeframe 5m --require-order --require-supabase
```

The Supabase portion uses the anonymous key and validates that dashboard-safe rows are visible through RLS. If this fails while local rows pass, inspect `publish_outbox` and `publish_failures` before changing dashboard code.

---

## 5. Dashboard Validation

From `apps/operational-metrics-dashboard`:

```bash
npm run lint
npm run build
npx next dev --port 3007
```

Open `http://localhost:3007` with Supabase mode enabled and verify:

- Live view shows real equity snapshot, open positions, latest order events, and freshness.
- Charts view shows real 5-minute bars, trade markers, and strategy annotations.
- Operations view shows execution quality and publish state.
- Performance view shows real trade summaries.
- Dark and light themes remain legible.
- Browser network requests use only the anon key.

---

## 6. Failure Simulation

To validate degradation behavior:

1. Temporarily set an invalid bot-side `DASHBOARD__SUPABASE_URL` or service key.
2. Let the bot enqueue dashboard rows.
3. Confirm `publish_outbox` rows move to `failed` or `dead_letter` with errors recorded in `publish_failures`.
4. Run cooldown or `flush_pending(...)` through the bot lifecycle.
5. Confirm Discord receives the dashboard publication degraded alert when unresolved rows remain.
6. Restore Supabase settings and confirm publication resumes without duplicate remote records.

---

## 7. Completion Criteria

Mark Stage 5 complete only after real paper-trading data satisfies all checklist items in [live-dashboard-execution-checklist.md](live-dashboard-execution-checklist.md). Until Supabase is set up and the bot is publishing to it, Stage 5 should remain in progress.