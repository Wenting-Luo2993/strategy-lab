# Live Dashboard Execution Checklist

**Last Updated:** 2026-07-27  
**Status:** Stage 5 End-to-End Validation In Progress  
**Related TDS:** [TDS-live-dashboard.md](TDS-live-dashboard.md)

This document is the working implementation tracker for the Phase 1 live trading dashboard. Update checkboxes and stage status here as work progresses; only update the TDS when the approved design itself changes.

---

## Stage 1: Persistence Foundation

- [x] Add `DashboardSettings` configuration fields in `vibe/trading_bot/config/settings.py` for enablement, account ID, symbols, timeframes, local DB paths, remote provider, publish interval, and retention window.
- [x] Add `PriceBarStore` with SQLite WAL mode, `(symbol, timeframe, bar_start)` idempotent upsert, `provider`, `ingestion_time`, and `is_complete` fields.
- [x] Add account, position, and equity snapshot stores with account-scoped primary keys and immutable domain timestamps.
- [x] Extend trade persistence with `account_id`, broker order linkage, `exit_reason`, and migration/backfill support for existing local rows.
- [x] Add `OrderEventStore` for broker-neutral order lifecycle events, including expected price, fill price, slippage, latency, raw broker status, and `occurred_at`.
- [x] Add durable `PublishOutboxStore` with retry state, idempotency keys, stale `publishing` recovery fields, and short WAL-mode transactions.
- [x] Add unit and migration tests for price bars, account snapshots, positions, equity snapshots, order events, trade migration/backfill, and outbox enqueue/claim behavior.
- [x] Update local documentation or operator notes with the new SQLite files and retention expectations. See [Live Dashboard Operator Notes](live-dashboard-operator-notes.md).

Stage 1 exit criteria:

- [x] Local stores can be created from a clean checkout without manual DB setup.
- [x] Re-running the same insert/upsert paths does not duplicate price bars, order events, trades, snapshots, or outbox events.
- [x] Existing trade DB rows can be migrated or backfilled with the configured Phase 1 account ID without data loss.
- [x] Store tests pass locally.

---

## Stage 2: Bot Integration

- [x] Wire `PriceBarStore` into the existing market-data polling/bar builder path and persist completed dashboard timeframe bars for configured symbols.
- [x] Record order lifecycle events from broker/order manager callbacks for submitted, filled, cancelled, and trade-closed states.
- [x] Link order events to trades when the strategy/order manager has the relationship available.
- [x] Persist account, position, and equity snapshots after fills and on the configured polling cadence.
- [x] Enqueue publish outbox events immediately after each local source-of-truth write, using stable aggregate IDs and provider-neutral payloads.
- [x] Signal the in-process publisher wake event after successful outbox enqueue without waiting for remote publication.
- [x] Ensure outbox enqueue failure is logged as telemetry degradation and does not block order submission, fill handling, market-data polling, or risk management.
- [x] Preserve provider name, ingestion timestamps, and original domain/event timestamps through every enqueue path.
- [x] Add focused integration tests or paper-mode smoke tests proving bot actions write local rows and outbox events.

Stage 2 exit criteria:

- [x] Paper-mode market-data polling writes at least one completed bar per configured dashboard symbol.
- [x] A paper order writes order events, trade updates, fill-quality metrics, and equity/position snapshots.
- [x] Every dashboard-relevant local row has a corresponding pending outbox event or is discoverable by reconciliation.
- [x] Trading flow remains functional when remote publishing is disabled or misconfigured.

---

## Stage 3: Remote Read Model

- [x] Define Supabase dashboard tables/views for accounts, trades, order events, price bars, equity snapshots, positions, strategy annotations, and operational metrics. See [Supabase Read Model SQL](supabase-read-model.sql).
- [x] Add RLS policies that allow bot-only writes through service credentials and anonymous read-only access to dashboard-safe rows/views.
- [x] Implement `RemoteDataPublisher` as an in-process asyncio background worker with bounded batch size, request timeouts, retry backoff, and circuit-breaker behavior.
- [x] Implement idempotent remote upserts for trades, order events, price bars, equity snapshots, positions, and metrics.
- [x] Implement durable publish failure logging with payload type, destination, retry count, error, and timestamp.
- [x] Add application-level wake signaling after enqueue plus periodic polling fallback.
- [x] Add `flush_pending(timeout_seconds, max_batches)` for bounded cooldown publishing.
- [x] Add `reconcile_sources(source_stores, trading_day)` to reconstruct missing outbox events from source rows using original domain timestamps.
- [x] Add `prune_published_before(cutoff_timestamp)` that never deletes unpublished or failed source rows.
- [x] Add cooldown summary telemetry for unresolved failed, pending, or dead-letter rows.
- [x] Add Discord notification for unresolved failed, pending, or dead-letter rows.
- [x] Add tests for claim flow, stale `publishing` recovery, retry scheduling, idempotent upserts, timestamp preservation, and cooldown reconciliation.

Stage 3 exit criteria:

- [x] Publisher restarts resume pending rows from SQLite without duplicate remote records.
- [x] Remote failures never block trading-path operations.
- [x] Cooldown leaves each due row in `published`, retry-scheduled, or `dead_letter` state with an error reason.
- [x] Dashboard-relevant domain timestamps remain unchanged across retry and reconciliation.

---

## Stage 4: Static Dashboard

- [x] Reuse `apps/operational-metrics-dashboard` as the live dashboard app and keep static export/GitHub Pages deployment working.
- [x] Add frontend data adapter boundary under `src/data/` with shared types, Supabase implementation, and static JSON fallback implementation.
- [x] Add fixture data covering live market day, closed market review, empty state, and remote unavailable state.
- [x] Build Live View with equity snapshot summary, open positions, latest order events, and data freshness status.
- [x] Build Charts view with TradingView Lightweight Charts, price bars, trade markers, and strategy annotations.
- [x] Build Operations view with health metrics, API latency, data freshness, publish status, and execution-quality metrics.
- [x] Build Performance view with precomputed performance metrics and trade summaries.
- [x] Add semantic light/dark theme tokens, system preference detection, local-storage preference persistence, and visible theme toggle.
- [x] Route chart/table health, profit, loss, warning, and freshness colors through theme tokens.
- [ ] Add dashboard tests for static build, fixture rendering, non-empty chart rendering, empty states, and unavailable remote store states.

Stage 4 exit criteria:

- [x] Static Next.js build succeeds.
- [x] Dashboard renders fixture data without requiring broker or Supabase credentials.
- [ ] Dashboard can read real published paper-trading rows through the configured data adapter. Supabase anon/RLS readback passed for accounts, trades, order events, price bars, equity snapshots, positions, and operational metrics on 2026-07-27; dashboard app adapter/browser verification remains open.
- [x] Light and dark themes are legible for charts, tables, trading states, and health states.

---

## Stage 5: End-to-End Validation

Runbook: [Stage 5 Live Dashboard Validation Runbook](stage-5-validation-runbook.md)

- [x] Run IB paper path with dashboard persistence and publication enabled. On 2026-07-27 the Oracle VM production bot was restarted with Stage 5 enabled, `Dashboard RemoteDataPublisher started`, and the bot ran against IBKR paper Gateway on `127.0.0.1:4002`.
- [x] Persist at least one completed 5-minute OHLCV bar for each configured dashboard symbol. Stage 5 validation found 2 local `price_bars` rows and 2 Supabase-visible `price_bars` rows for configured symbol `QQQ`.
- [x] Submit a paper order and verify order event, trade, fill metric, position snapshot, and equity snapshot rows locally. The live bot submitted and filled a paper `SELL 1 QQQ`; local validation found `trades=1`, `order_events=2`, `positions=1`, `equity_snapshots=9`, and `operational_metrics=26`.
- [x] Publish read model to Supabase or static JSON and verify idempotent remote keys. Supabase validation passed with visible rows: accounts=3, trades=1, order_events=2, price_bars=2, equity_snapshots=9, positions=1, operational_metrics=5; local outbox was fully drained with `{'published': 29}` and unresolved=0.
- [ ] Build static dashboard and verify charts, positions, equity, trade feed, operations, and performance panels against real published rows.
- [ ] Simulate remote publish failure and confirm retries, dashboard degraded state, local failure log, cooldown reconciliation, and Discord escalation. Partial real failure evidence exists: a Supabase schema mismatch opened the remote publisher circuit breaker, recorded local `publish_failures`, and was recovered after filtering non-schema `reason` metadata from `equity_snapshot` and `position` payloads; dashboard degraded-state UI and Discord escalation still need explicit validation.
- [ ] Deploy GitHub Pages dashboard and confirm no service credentials are present in browser bundles or public config.
- [x] Document operator setup, required environment variables, Supabase/RLS setup, local DB locations, recovery steps, and known fallback path.

Stage 5 exit criteria:

- [ ] Phase 1 acceptance criteria are satisfied with real paper-trading data. Backend read-model generation is validated; static dashboard UI verification against the real rows remains open.
- [ ] The dashboard remains read-only and uses only browser-safe credentials. Supabase anon/RLS readback is validated; public bundle/config audit remains open.
- [ ] Operator documentation is sufficient to recover from remote publication outage or Supabase pause.
- [ ] Implementation status is ready to move from Phase 1 buildout to maintenance and iteration.