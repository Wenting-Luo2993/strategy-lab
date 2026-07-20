# Live Dashboard Operator Notes

**Last Updated:** 2026-07-20  
**Related TDS:** [TDS-live-dashboard.md](TDS-live-dashboard.md)  
**Execution Tracker:** [live-dashboard-execution-checklist.md](live-dashboard-execution-checklist.md)

These notes cover the local persistence files introduced for the Phase 1 live trading dashboard. The local files are durability buffers for the bot and publisher worker; they are not the long-term dashboard system of record.

---

## Local SQLite Files

| File | Setting | Purpose |
|------|---------|---------|
| `./data/market_data.db` | `dashboard.local_price_db_path` | Completed OHLCV bars for configured dashboard symbols and timeframes. |
| `./data/dashboard.db` | `dashboard.local_dashboard_db_path` | Dashboard account rows, equity snapshots, latest positions, and order events. |
| `./data/local/publish_outbox.db` | `dashboard.local_outbox_db_path` | Durable queue of remote publication events and retry state. |
| `./data/trades.db` | `database_path` | Existing trade store, now migrated with dashboard fields such as `account_id`, `broker_order_id`, and `exit_reason`. |

The stores create parent directories and SQLite schemas automatically on first use. No manual database setup is required for local development.

---

## Retention Expectations

- Local dashboard persistence defaults to `dashboard.local_retention_days = 3`.
- Local rows are short-lived buffers that should survive transient remote outages during the trading day.
- Published rows can be pruned after the configured retention window once reconciliation confirms they are safely remote or intentionally dead-lettered.
- Unpublished, failed, or retry-scheduled rows must not be deleted by retention cleanup.
- Domain timestamps such as `bar_start`, `occurred_at`, equity snapshot `timestamp`, position `updated_at`, `entry_time`, and `exit_time` must remain unchanged across retries and reconciliation.

---

## Failure Handling

Remote publication failures are telemetry degradation, not trading failures. The trading path should persist the source row first, enqueue a publish event when possible, log enqueue or publish failures, and continue order submission, fill handling, market-data polling, and risk management.

If the outbox enqueue fails because SQLite is briefly locked, cooldown reconciliation should later scan the local source tables and reconstruct missing publish events using the original source-row timestamps.

Operational response during an outage:

1. Confirm trading-path local source rows are still being written.
2. Inspect `publish_outbox` status counts for `pending`, `failed`, `publishing`, and `dead_letter` rows.
3. Let cooldown reconciliation retry due rows before pruning local dashboard data.
4. Treat unresolved failed or dead-letter rows as dashboard degradation and alert through the standard Discord notification path once Stage 3 publishing is implemented.