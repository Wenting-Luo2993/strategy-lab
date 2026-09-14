"""In-process publisher for live dashboard outbox events."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Optional, Protocol

from vibe.trading_bot.storage.dashboard_store import PublishOutboxEvent, PublishOutboxStore

logger = logging.getLogger(__name__)


class PublishOutcome(str, Enum):
    """Result of a remote write after destination-side version fencing."""

    APPLIED = "applied"
    STALE_REJECTED = "stale_rejected"


class RemotePublishDestination(Protocol):
    """Remote write destination used by RemoteDataPublisher."""

    async def publish(self, event: Dict[str, Any]) -> PublishOutcome:
        """Publish one claimed outbox event or raise on transport failure."""
        ...


class SupabaseRestDestination:
    """Supabase REST destination using service-role credentials."""

    TABLE_BY_AGGREGATE = {
        "account": "accounts",
        "trade": "trades",
        "order_event": "order_events",
        "price_bar": "price_bars",
        "equity_snapshot": "equity_snapshots",
        "position": "positions",
        "metric": "operational_metrics",
        "strategy_annotation": "strategy_annotations",
    }

    CONFLICT_BY_AGGREGATE = {
        "account": "account_id",
        "trade": "trade_id",
        "order_event": "event_id",
        "price_bar": "symbol,timeframe,bar_start",
        "equity_snapshot": "snapshot_id",
        "position": "position_id",
        "metric": "metric_id",
        "strategy_annotation": "annotation_id",
    }

    PAYLOAD_COLUMNS_BY_AGGREGATE = {
        "equity_snapshot": {
            "snapshot_id",
            "account_id",
            "timestamp",
            "net_liquidation",
            "cash",
            "buying_power",
            "realized_pnl",
            "unrealized_pnl",
            "base_currency",
            "net_liquidation_currency",
            "cash_currency",
            "buying_power_currency",
            "realized_pnl_currency",
            "unrealized_pnl_currency",
            "pnl_provenance",
            "realized_pnl_provenance",
            "unrealized_pnl_provenance",
            "pnl_version",
            "local_realized_pnl",
            "local_realized_pnl_currency",
            "granularity",
            "period_start",
            "event_type",
            "source",
        },
        "position": {
            "position_id",
            "account_id",
            "symbol",
            "quantity",
            "side",
            "avg_cost",
            "market_price",
            "unrealized_pnl",
            "instrument_currency",
            "unrealized_pnl_currency",
            "updated_at",
        },
        "metric": {
            "metric_id",
            "metric_name",
            "metric_value",
            "dimensions",
            "timestamp",
        },
    }

    def __init__(self, url: str, service_key: str, request_timeout_seconds: float = 10.0):
        self.url = url.rstrip("/")
        self.service_key = service_key
        self.request_timeout_seconds = request_timeout_seconds

    async def publish(self, event: Dict[str, Any]) -> PublishOutcome:
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError("aiohttp is required for SupabaseRestDestination") from exc

        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
            # The returned row is required to tell an applied update from the
            # OLD row returned by the server-side stale-version fence.
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            aggregate_type = event["aggregate_type"]
            if aggregate_type == "equity_snapshot_delete":
                snapshot_ids = [str(item) for item in event["payload"].get("snapshot_ids", [])]
                if not snapshot_ids:
                    return PublishOutcome.APPLIED
                endpoint = f"{self.url}/rest/v1/equity_snapshots"
                request = session.delete(
                    endpoint,
                    params={"snapshot_id": f"in.({','.join(snapshot_ids)})"},
                    headers=headers,
                )
            else:
                table = self.TABLE_BY_AGGREGATE[aggregate_type]
                on_conflict = self.CONFLICT_BY_AGGREGATE[aggregate_type]
                payload = self._payload_for_aggregate(aggregate_type, event["payload"])
                payload["publication_version"] = int(
                    event.get("publication_version") or 1
                )
                endpoint = f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}"
                request = session.post(endpoint, json=payload, headers=headers)
            async with request as response:
                if 200 <= response.status < 300:
                    if aggregate_type == "equity_snapshot_delete":
                        return PublishOutcome.APPLIED
                    rows = await response.json(content_type=None)
                    expected_version = int(payload["publication_version"])
                    return self._upsert_outcome(rows, expected_version)
                body = await response.text()
                raise RuntimeError(f"Supabase publish failed: status={response.status} body={body}")

    def _payload_for_aggregate(self, aggregate_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        columns = self.PAYLOAD_COLUMNS_BY_AGGREGATE.get(aggregate_type)
        if columns is None:
            return payload
        return {key: value for key, value in payload.items() if key in columns}

    @staticmethod
    def _upsert_outcome(rows: Any, expected_version: int) -> PublishOutcome:
        returned_version = (
            int(rows[0]["publication_version"])
            if isinstance(rows, list)
            and rows
            and rows[0].get("publication_version") is not None
            else None
        )
        return (
            PublishOutcome.APPLIED
            if returned_version == expected_version
            else PublishOutcome.STALE_REJECTED
        )


@dataclass
class PublishBatchResult:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0
    stale_rejected: int = 0


class RemoteDataPublisher:
    """Drain dashboard outbox rows and publish them outside the trading path."""

    def __init__(
        self,
        outbox_store: PublishOutboxStore,
        destination: RemotePublishDestination,
        wake_event: Optional[asyncio.Event] = None,
        batch_size: int = 25,
        poll_interval_seconds: float = 300.0,
        max_attempts: int = 5,
        retry_base_seconds: float = 30.0,
        circuit_breaker_failures: int = 5,
        circuit_breaker_cooldown_seconds: float = 300.0,
        claimed_by: str = "remote-data-publisher",
        published_retention_days: int = 7,
        prune_batch_size: int = 500,
        prune_interval_seconds: float = 3600.0,
        claim_timeout_seconds: float = 300.0,
    ):
        self.outbox_store = outbox_store
        self.destination = destination
        self.wake_event = wake_event or asyncio.Event()
        self.batch_size = batch_size
        self.poll_interval_seconds = poll_interval_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.circuit_breaker_failures = circuit_breaker_failures
        self.circuit_breaker_cooldown_seconds = circuit_breaker_cooldown_seconds
        self.claimed_by = claimed_by
        self.published_retention_days = published_retention_days
        self.prune_batch_size = prune_batch_size
        self.prune_interval_seconds = prune_interval_seconds
        self.claim_timeout_seconds = claim_timeout_seconds
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._consecutive_failures = 0
        self._circuit_open_until: Optional[datetime] = None
        self._last_prune_at: Optional[datetime] = None
        self._flush_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._reset_stale_claims()
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        self.wake_event.set()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await self.flush_pending(timeout_seconds=30.0, max_batches=1)
            self.run_retention_maintenance()
            self.wake_event.clear()
            try:
                await asyncio.wait_for(self.wake_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _circuit_is_open(self) -> bool:
        if self._circuit_open_until is None:
            return False
        if datetime.now(timezone.utc) >= self._circuit_open_until:
            self._circuit_open_until = None
            self._consecutive_failures = 0
            return False
        return True

    def _retry_at(self, attempts_after_failure: int) -> datetime:
        delay = self.retry_base_seconds * (2 ** max(attempts_after_failure - 1, 0))
        return datetime.now(timezone.utc) + timedelta(seconds=delay)

    async def flush_pending(self, timeout_seconds: float, max_batches: int) -> PublishBatchResult:
        """Serialize drains so an older remote write cannot overtake a newer one."""
        async with self._flush_lock:
            return await self._flush_pending_unlocked(timeout_seconds, max_batches)

    async def _flush_pending_unlocked(
        self,
        timeout_seconds: float,
        max_batches: int,
    ) -> PublishBatchResult:
        self._reset_stale_claims()
        result = PublishBatchResult()
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        batches = 0

        while batches < max_batches and datetime.now(timezone.utc) < deadline:
            if self._circuit_is_open():
                break
            batch = self.outbox_store.claim_pending(limit=self.batch_size, claimed_by=self.claimed_by)
            if not batch:
                break
            batches += 1
            result.claimed += len(batch)
            for event in batch:
                try:
                    outcome = await self.destination.publish(event)
                    # Backward-compatible custom destinations historically
                    # returned None on success. Supabase always returns an
                    # explicit fenced outcome.
                    if outcome == PublishOutcome.STALE_REJECTED:
                        marked = self.outbox_store.mark_stale_rejected(
                            event["event_id"],
                            expected_payload_version=int(event["payload_version"]),
                        )
                        self._consecutive_failures = 0
                        if marked:
                            result.stale_rejected += 1
                        continue
                    marked = self.outbox_store.mark_published(
                        event["event_id"],
                        expected_payload_version=int(event["payload_version"]),
                    )
                    self._consecutive_failures = 0
                    if marked:
                        result.published += 1
                except Exception as exc:
                    error = str(exc)
                    attempts_after_failure = int(event.get("attempts") or 0) + 1
                    self.outbox_store.record_failure(event, error)
                    self._consecutive_failures += 1
                    if attempts_after_failure >= self.max_attempts:
                        marked = self.outbox_store.mark_dead_letter(
                            event["event_id"],
                            error,
                            expected_payload_version=int(event["payload_version"]),
                        )
                        if marked:
                            result.dead_lettered += 1
                    else:
                        marked = self.outbox_store.mark_failed(
                            event["event_id"],
                            error,
                            self._retry_at(attempts_after_failure),
                            expected_payload_version=int(event["payload_version"]),
                        )
                        if marked:
                            result.failed += 1
                    if self._consecutive_failures >= self.circuit_breaker_failures:
                        self._circuit_open_until = datetime.now(timezone.utc) + timedelta(
                            seconds=self.circuit_breaker_cooldown_seconds
                        )
                        logger.warning("Remote publisher circuit opened until %s", self._circuit_open_until)
                        break
        return result

    def _reset_stale_claims(self) -> int:
        claimed_before = datetime.now(timezone.utc) - timedelta(
            seconds=self.claim_timeout_seconds
        )
        reset = self.outbox_store.reset_stale_publishing(claimed_before)
        if reset:
            logger.warning("Reset %s stale dashboard publication claims", reset)
        return reset

    def reconcile_sources(self, source_stores: Iterable[Any], trading_day: Any) -> int:
        enqueued = 0
        for source_store in source_stores:
            if not hasattr(source_store, "iter_publish_events"):
                continue
            events = list(source_store.iter_publish_events(trading_day))
            typed_events = [event for event in events if isinstance(event, PublishOutboxEvent)]
            enqueued += self.outbox_store.enqueue_many(typed_events)
        if enqueued:
            self.wake_event.set()
        return enqueued

    def prune_published_before(self, cutoff_timestamp: datetime | str, batch_size: Optional[int] = None) -> int:
        return self.outbox_store.prune_published_before(
            cutoff_timestamp,
            batch_size=batch_size or self.prune_batch_size,
        )

    def run_retention_maintenance(self, *, force: bool = False, max_batches: int = 10) -> int:
        """Prune old confirmed rows in bounded batches and report operational timing."""
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._last_prune_at is not None
            and (now - self._last_prune_at).total_seconds() < self.prune_interval_seconds
        ):
            return 0
        started = time.monotonic()
        cutoff = now - timedelta(days=self.published_retention_days)
        deleted = 0
        try:
            for _ in range(max_batches):
                batch_deleted = self.prune_published_before(cutoff)
                deleted += batch_deleted
                if batch_deleted < self.prune_batch_size:
                    break
        except sqlite3.Error:
            logger.exception("Published outbox pruning failed")
            return 0
        self._last_prune_at = now
        logger.info(
            "Published outbox pruning complete: deleted=%s duration_ms=%.1f cutoff=%s",
            deleted,
            (time.monotonic() - started) * 1000.0,
            cutoff.isoformat(),
        )
        return deleted

    def publish_cooldown_summary(self) -> Dict[str, int]:
        return self.outbox_store.status_counts()