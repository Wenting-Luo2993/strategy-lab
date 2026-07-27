"""In-process publisher for live dashboard outbox events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional, Protocol

from vibe.trading_bot.storage.dashboard_store import PublishOutboxEvent, PublishOutboxStore

logger = logging.getLogger(__name__)


class RemotePublishDestination(Protocol):
    """Remote write destination used by RemoteDataPublisher."""

    async def publish(self, event: Dict[str, Any]) -> None:
        """Publish one claimed outbox event or raise on failure."""
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
        "metric": "metric_name,timestamp",
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
            "updated_at",
        },
    }

    def __init__(self, url: str, service_key: str, request_timeout_seconds: float = 10.0):
        self.url = url.rstrip("/")
        self.service_key = service_key
        self.request_timeout_seconds = request_timeout_seconds

    async def publish(self, event: Dict[str, Any]) -> None:
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError("aiohttp is required for SupabaseRestDestination") from exc

        aggregate_type = event["aggregate_type"]
        table = self.TABLE_BY_AGGREGATE[aggregate_type]
        on_conflict = self.CONFLICT_BY_AGGREGATE[aggregate_type]
        payload = self._payload_for_aggregate(aggregate_type, event["payload"])
        endpoint = f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}"
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, json=payload, headers=headers) as response:
                if 200 <= response.status < 300:
                    return
                body = await response.text()
                raise RuntimeError(f"Supabase publish failed: status={response.status} body={body}")

    def _payload_for_aggregate(self, aggregate_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        columns = self.PAYLOAD_COLUMNS_BY_AGGREGATE.get(aggregate_type)
        if columns is None:
            return payload
        return {key: value for key, value in payload.items() if key in columns}


@dataclass
class PublishBatchResult:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0


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
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._consecutive_failures = 0
        self._circuit_open_until: Optional[datetime] = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
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
            self.wake_event.clear()
            try:
                await asyncio.wait_for(self.wake_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _circuit_is_open(self) -> bool:
        if self._circuit_open_until is None:
            return False
        if datetime.utcnow() >= self._circuit_open_until:
            self._circuit_open_until = None
            self._consecutive_failures = 0
            return False
        return True

    def _retry_at(self, attempts_after_failure: int) -> datetime:
        delay = self.retry_base_seconds * (2 ** max(attempts_after_failure - 1, 0))
        return datetime.utcnow() + timedelta(seconds=delay)

    async def flush_pending(self, timeout_seconds: float, max_batches: int) -> PublishBatchResult:
        result = PublishBatchResult()
        deadline = datetime.utcnow() + timedelta(seconds=timeout_seconds)
        batches = 0

        while batches < max_batches and datetime.utcnow() < deadline:
            if self._circuit_is_open():
                break
            batch = self.outbox_store.claim_pending(limit=self.batch_size, claimed_by=self.claimed_by)
            if not batch:
                break
            batches += 1
            result.claimed += len(batch)
            for event in batch:
                try:
                    await self.destination.publish(event)
                    self.outbox_store.mark_published(event["event_id"])
                    self._consecutive_failures = 0
                    result.published += 1
                except Exception as exc:
                    error = str(exc)
                    attempts_after_failure = int(event.get("attempts") or 0) + 1
                    self.outbox_store.record_failure(event, error)
                    self._consecutive_failures += 1
                    if attempts_after_failure >= self.max_attempts:
                        self.outbox_store.mark_dead_letter(event["event_id"], error)
                        result.dead_lettered += 1
                    else:
                        self.outbox_store.mark_failed(event["event_id"], error, self._retry_at(attempts_after_failure))
                        result.failed += 1
                    if self._consecutive_failures >= self.circuit_breaker_failures:
                        self._circuit_open_until = datetime.utcnow() + timedelta(
                            seconds=self.circuit_breaker_cooldown_seconds
                        )
                        logger.warning("Remote publisher circuit opened until %s", self._circuit_open_until)
                        break
        return result

    def reconcile_sources(self, source_stores: Iterable[Any], trading_day: Any) -> int:
        enqueued = 0
        for source_store in source_stores:
            if not hasattr(source_store, "iter_publish_events"):
                continue
            events = list(source_store.iter_publish_events(trading_day))
            typed_events = [event for event in events if isinstance(event, PublishOutboxEvent)]
            self.outbox_store.enqueue_many(typed_events)
            enqueued += len(typed_events)
        if enqueued:
            self.wake_event.set()
        return enqueued

    def prune_published_before(self, cutoff_timestamp: datetime | str) -> int:
        return self.outbox_store.prune_published_before(cutoff_timestamp)

    def publish_cooldown_summary(self) -> Dict[str, int]:
        return self.outbox_store.status_counts()