"""Operational metrics recording for broker execution quality."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Protocol

from vibe.trading_bot.brokers.base import FillEvent
from vibe.trading_bot.release_metadata import release_metadata
from vibe.trading_bot.storage.metrics_store import MetricType, MetricsStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationalMetric:
    """Single operational metric sample."""

    name: str
    value: float
    dimensions: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    idempotency_key: Optional[str] = None


class RemoteMetricsSink(Protocol):
    """Remote cloud metrics sink contract."""

    async def record_metric(self, metric: OperationalMetric) -> bool:
        """Record one metric in a remote service."""
        ...


class SupabaseRestMetricsSink:
    """Zero-cost-friendly Supabase REST sink for operational metrics."""

    def __init__(
        self,
        url: str,
        anon_key: str,
        table_name: str = "operational_metrics",
        release: Optional[Dict[str, str]] = None,
    ):
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.table_name = table_name
        self.release = dict(release or release_metadata())

    async def record_metric(self, metric: OperationalMetric) -> bool:
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError("aiohttp is required for SupabaseRestMetricsSink") from exc

        payload = self._payload_for_metric(metric)
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

    def _payload_for_metric(self, metric: OperationalMetric) -> Dict[str, object]:
        return {
            "metric_id": metric.idempotency_key or (
                f"legacy:{metric.name}:{metric.timestamp.isoformat()}"
            ),
            "metric_name": metric.name,
            "metric_value": metric.value,
            "dimensions": metric.dimensions,
            "timestamp": metric.timestamp.isoformat(),
            **self.release,
        }


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
                idempotency_key=metric.idempotency_key,
            )

        if self.remote_sink is not None:
            await self.remote_sink.record_metric(metric)

    async def record_fill_event(self, event: FillEvent) -> None:
        """Record each execution independently; never collapse partial fills."""
        executions = list(event.executions) or [{
            "execution_id": event.execution_id,
            "broker_order_id": event.broker_order_id,
            "symbol": event.symbol,
            "side": event.side,
            "quantity": event.quantity,
            "price": event.avg_fill_price,
            "filled_at": event.filled_at,
            "trade_currency": event.instrument_currency,
            "commission": event.commission,
            "commission_currency": event.commission_currency,
        }]
        benchmark_valid = (
            event.benchmark_version == 2
            and event.benchmark_valid
            and event.benchmark_price not in (None, 0)
        )
        for execution in executions:
            execution_id = execution.get("execution_id")
            identity = str(execution_id) if execution_id else f"legacy:{event.broker_order_id}"
            filled_at = execution.get("filled_at") or event.filled_at
            if isinstance(filled_at, str):
                filled_at = datetime.fromisoformat(filled_at)
            price = float(execution.get("price") or event.avg_fill_price)
            quantity = float(execution.get("quantity") or 0.0)
            dimensions = {
                "account_id": str(
                    execution.get("account_id") or event.account_id or ""
                ),
                "broker": "interactive_brokers",
                "symbol": execution.get("symbol") or event.symbol,
                "side": execution.get("side") or event.side,
                "broker_order_id": str(execution.get("broker_order_id") or event.broker_order_id),
                "execution_id": str(execution_id or event.broker_order_id),
                "status": event.raw_status,
                "trade_currency": execution.get("trade_currency") or event.instrument_currency or "unknown",
                "commission_currency": execution.get("commission_currency") or "unknown",
                "slippage_version": str(event.benchmark_version),
                "slippage_valid": str(benchmark_valid).lower(),
            }
            latency_ms = max((filled_at - event.submitted_at).total_seconds() * 1000.0, 0.0)
            metrics = [
                OperationalMetric("actual_fill_price", price, dimensions, filled_at, f"{identity}:actual_fill_price"),
                OperationalMetric("fill_quantity", quantity, dimensions, filled_at, f"{identity}:fill_quantity"),
                OperationalMetric("latency_ms", latency_ms, dimensions, filled_at, f"{identity}:latency_ms"),
            ]
            commission = execution.get("commission")
            if commission is not None:
                metrics.append(
                    OperationalMetric(
                        "commission",
                        float(commission),
                        dimensions,
                        filled_at,
                        f"{identity}:commission",
                    )
                )
            if benchmark_valid:
                benchmark = float(event.benchmark_price)
                slippage = price - benchmark if event.side == "buy" else benchmark - price
                metrics.extend([
                    OperationalMetric("expected_fill_price", benchmark, dimensions, filled_at, f"{identity}:expected_fill_price"),
                    OperationalMetric("slippage", slippage, dimensions, filled_at, f"{identity}:slippage"),
                    OperationalMetric("slippage_bps", (slippage / benchmark) * 10000.0, dimensions, filled_at, f"{identity}:slippage_bps"),
                ])
            for metric in metrics:
                await self.record_metric(metric)
