"""Operational metrics recording for broker execution quality."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Protocol

from vibe.trading_bot.brokers.base import FillEvent
from vibe.trading_bot.storage.metrics_store import MetricType, MetricsStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationalMetric:
    """Single operational metric sample."""

    name: str
    value: float
    dimensions: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


class RemoteMetricsSink(Protocol):
    """Remote cloud metrics sink contract."""

    async def record_metric(self, metric: OperationalMetric) -> bool:
        """Record one metric in a remote service."""
        ...


class SupabaseRestMetricsSink:
    """Zero-cost-friendly Supabase REST sink for operational metrics."""

    def __init__(self, url: str, anon_key: str, table_name: str = "operational_metrics"):
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.table_name = table_name

    async def record_metric(self, metric: OperationalMetric) -> bool:
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError("aiohttp is required for SupabaseRestMetricsSink") from exc

        payload = {
            "metric_name": metric.name,
            "metric_value": metric.value,
            "dimensions": metric.dimensions,
            "timestamp": metric.timestamp.isoformat(),
        }
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        endpoint = f"{self.url}/rest/v1/{self.table_name}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, json=payload, headers=headers, timeout=10) as response:
                    if 200 <= response.status < 300:
                        return True
                    body = await response.text()
                    logger.warning("Remote metrics write failed: status=%s body=%s", response.status, body)
                    return False
        except Exception as exc:
            logger.warning("Remote metrics write failed: %s", exc)
            return False


class OperationalMetricsRecorder:
    """Record execution quality metrics locally and optionally to a remote cloud DB."""

    def __init__(
        self,
        local_store: Optional[MetricsStore] = None,
        remote_sink: Optional[RemoteMetricsSink] = None,
    ):
        self.local_store = local_store
        self.remote_sink = remote_sink

    async def record_metric(self, metric: OperationalMetric) -> None:
        if self.local_store is not None:
            self.local_store.record_metric(
                metric_type=MetricType.TRADE.value,
                metric_name=metric.name,
                metric_value=metric.value,
                dimensions=metric.dimensions,
                timestamp=metric.timestamp.isoformat(),
            )

        if self.remote_sink is not None:
            await self.remote_sink.record_metric(metric)

    async def record_fill_event(self, event: FillEvent) -> None:
        """Record expected fill, actual fill, slippage, commission, and latency."""
        dimensions = {
            "broker": "interactive_brokers",
            "symbol": event.symbol,
            "side": event.side,
            "broker_order_id": event.broker_order_id,
            "status": event.raw_status,
        }

        metrics = [
            OperationalMetric("actual_fill_price", event.avg_fill_price, dimensions, event.filled_at),
            OperationalMetric("fill_quantity", event.quantity, dimensions, event.filled_at),
            OperationalMetric("latency_ms", event.latency_ms, dimensions, event.filled_at),
            OperationalMetric("commission", event.commission, dimensions, event.filled_at),
        ]

        if event.expected_price is not None:
            metrics.extend(
                [
                    OperationalMetric("expected_fill_price", event.expected_price, dimensions, event.filled_at),
                    OperationalMetric("slippage", event.slippage or 0.0, dimensions, event.filled_at),
                    OperationalMetric("slippage_bps", event.slippage_bps or 0.0, dimensions, event.filled_at),
                ]
            )

        for metric in metrics:
            await self.record_metric(metric)
