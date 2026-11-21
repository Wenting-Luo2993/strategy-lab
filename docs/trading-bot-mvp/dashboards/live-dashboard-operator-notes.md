<!-- taskboard
{"id":"dashboard-operations","title":"Live dashboard operations and recovery","projects":["live-reliability"],"status":"current","kind":"reference","reviewed":"2026-11-16"}
-->
# Live Dashboard Operator Notes

**Last Updated:** 2026-11-16
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
| `metric` | `operational_metrics` | `metric_id` |
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

### Metric identity conflicts (HTTP 409)

`23505` on the legacy `(metric_name, timestamp)` primary/unique key is a
permanent identity conflict, not a reason to restart Gateway or lengthen HTTP
timeouts. Different symbols intentionally share a metric name and timestamp.

1. Inventory all writers. Current bot publishers use `metric_id`; upgrade or
   retire any writer still using `on_conflict=metric_name,timestamp` first.
2. Obtain approval, back up Supabase and take a consistent SQLite outbox backup
   with SQLite's backup API (not a bare copy of a live WAL database).
3. Apply [supabase-metric-identity.sql](supabase-metric-identity.sql) after the base
   read-model migration, using a privileged SQL editor connection. It takes a
   bounded exclusive lock, verifies existing IDs and drops only name/time
   uniqueness. It preserves rows/IDs/timestamps and aborts on dependencies rather
   than cascading. Reapplication is safe. The updated base schema does not
   recreate the legacy index after finalization.
4. Verify constraints and same-name/time, different-ID upserts. Do not retry
   failed publications before the schema is corrected.
5. Deploy the reviewed bot/recovery code from `main` through the deployment
   harness. With separate operator approval, run
   `bash deploy/ibkr-docker/deploy.sh recover-dashboard-metrics`.
6. Wait for the existing publisher to consume the new pending successors;
   verify their `published` state and matching remote `metric_id` rows. The
   recovery command reports queueing only. It leaves unrelated failures alone.

The recovery preview is read-only. Applying it checks its fingerprint and each
latest dead-letter predecessor before enqueueing an immutable retry with the
original payload/time and a new publication version. Original dead-letter and
failure records are retained. Cooldown distinguishes `recovered_dead_letter`
from unresolved `dead_letter` via the durable publication ledger, including
after published successors have been pruned.

Rollback to a name/time-conflict-target publisher is **not compatible** with
this finalized schema. Re-adding legacy uniqueness can fail once distinct
same-name/time rows exist; do not delete those rows to force a rollback.