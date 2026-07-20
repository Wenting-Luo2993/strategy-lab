# Live Dashboard Execution Checklist

**Last Updated:** 2026-07-20  
**Status:** Stage 2 Complete  
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

- [ ] Define Supabase dashboard tables/views for accounts, trades, order events, price bars, equity snapshots, positions, strategy annotations, and operational metrics.
- [ ] Add RLS policies that allow bot-only writes through service credentials and anonymous read-only access to dashboard-safe rows/views.
- [ ] Implement `RemoteDataPublisher` as an in-process asyncio background worker with bounded batch size, request timeouts, retry backoff, and circuit-breaker behavior.
- [ ] Implement idempotent remote upserts for trades, order events, price bars, equity snapshots, positions, and metrics.
- [ ] Implement durable publish failure logging with payload type, destination, retry count, error, and timestamp.
- [ ] Add application-level wake signaling after enqueue plus periodic polling fallback.
- [ ] Add `flush_pending(timeout_seconds, max_batches)` for bounded cooldown publishing.
- [ ] Add `reconcile_sources(source_stores, trading_day)` to reconstruct missing outbox events from source rows using original domain timestamps.
- [ ] Add `prune_published_before(cutoff_timestamp)` that never deletes unpublished or failed source rows.
- [ ] Add cooldown summary telemetry and Discord notification for unresolved failed, pending, or dead-letter rows.
- [ ] Add tests for claim flow, stale `publishing` recovery, retry scheduling, idempotent upserts, timestamp preservation, and cooldown reconciliation.

Stage 3 exit criteria:

- [ ] Publisher restarts resume pending rows from SQLite without duplicate remote records.
- [ ] Remote failures never block trading-path operations.
- [ ] Cooldown leaves each due row in `published`, retry-scheduled, or `dead_letter` state with an error reason.
- [ ] Dashboard-relevant domain timestamps remain unchanged across retry and reconciliation.

---

## Stage 4: Static Dashboard

- [ ] Reuse `apps/operational-metrics-dashboard` as the live dashboard app and keep static export/GitHub Pages deployment working.
- [ ] Add frontend data adapter boundary under `src/data/` with shared types, Supabase implementation, and static JSON fallback implementation.
- [ ] Add fixture data covering live market day, closed market review, empty state, and remote unavailable state.
- [ ] Build Live View with equity snapshot summary, open positions, latest order events, and data freshness status.
- [ ] Build Charts view with TradingView Lightweight Charts, price bars, trade markers, and strategy annotations.
- [ ] Build Operations view with health metrics, API latency, data freshness, publish status, and execution-quality metrics.
- [ ] Build Performance view with precomputed performance metrics and trade summaries.
- [ ] Add semantic light/dark theme tokens, system preference detection, local-storage preference persistence, and visible theme toggle.
- [ ] Route chart/table health, profit, loss, warning, and freshness colors through theme tokens.
- [ ] Add dashboard tests for static build, fixture rendering, non-empty chart rendering, empty states, and unavailable remote store states.

Stage 4 exit criteria:

- [ ] Static Next.js build succeeds.
- [ ] Dashboard renders fixture data without requiring broker or Supabase credentials.
- [ ] Dashboard can read real published paper-trading rows through the configured data adapter.
- [ ] Light and dark themes are legible for charts, tables, trading states, and health states.

---

## Stage 5: End-to-End Validation

- [ ] Run IB paper smoke path with dashboard persistence and publication enabled.
- [ ] Persist at least one completed 5-minute OHLCV bar for each configured dashboard symbol.
- [ ] Submit a paper order and verify order event, trade, fill metric, position snapshot, and equity snapshot rows locally.
- [ ] Publish read model to Supabase or static JSON and verify idempotent remote keys.
- [ ] Build static dashboard and verify charts, positions, equity, trade feed, operations, and performance panels against real published rows.
- [ ] Simulate remote publish failure and confirm retries, dashboard degraded state, local failure log, cooldown reconciliation, and Discord escalation.
- [ ] Deploy GitHub Pages dashboard and confirm no service credentials are present in browser bundles or public config.
- [ ] Document operator setup, required environment variables, Supabase/RLS setup, local DB locations, recovery steps, and known fallback path.

Stage 5 exit criteria:

- [ ] Phase 1 acceptance criteria are satisfied with real paper-trading data.
- [ ] The dashboard remains read-only and uses only browser-safe credentials.
- [ ] Operator documentation is sufficient to recover from remote publication outage or Supabase pause.
- [ ] Implementation status is ready to move from Phase 1 buildout to maintenance and iteration.