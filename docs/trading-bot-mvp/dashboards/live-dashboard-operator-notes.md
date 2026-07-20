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

## Remote Publication

Stage 3 adds an in-process `RemoteDataPublisher` that drains `publish_outbox` outside the trading path. The publisher starts only when all of these are true:

- `dashboard.enabled = true`
- `dashboard.remote_provider = "supabase"`
- `dashboard.supabase_url` is configured
- `dashboard.supabase_service_key` is configured

The bot uses the Supabase service-role key only in the server-side trading process. Browser dashboard clients must use the anonymous key and RLS read policies from [Supabase Read Model SQL](supabase-read-model.sql). Never expose `dashboard.supabase_service_key` through static dashboard config or browser bundles.

The publisher uses bounded batches, request timeouts, exponential retry backoff, and a circuit breaker. Outbox enqueue wakes the publisher immediately, and `dashboard.publish_interval_seconds` provides a polling fallback if wake signaling is missed. Cooldown runs a bounded `flush_pending(...)` before provider disconnect.

If cooldown leaves unresolved `pending`, `failed`, `publishing`, or `dead_letter` rows, the bot sends a Discord `SystemAlertPayload` when `notifications.discord_webhook_url` is configured and `notifications.notify_on_error` is enabled. Dead-letter rows escalate the alert to `SYSTEM_ERROR`; other unresolved rows send `SYSTEM_WARNING`.

Remote upserts are idempotent by aggregate key:

| Aggregate | Remote table | Conflict key |
|-----------|--------------|--------------|
| `account` | `accounts` | `account_id` |
| `trade` | `trades` | `trade_id` |
| `order_event` | `order_events` | `event_id` |
| `price_bar` | `price_bars` | `symbol,timeframe,bar_start` |
| `equity_snapshot` | `equity_snapshots` | `snapshot_id` |
| `position` | `positions` | `position_id` |
| `metric` | `operational_metrics` | `metric_name,timestamp` |
| `strategy_annotation` | `strategy_annotations` | `annotation_id` |

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
4. Treat unresolved failed or dead-letter rows as dashboard degradation; cooldown sends the standard Discord alert when notification settings are configured.