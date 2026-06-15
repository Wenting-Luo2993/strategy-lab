"""Tests for broker contracts and operational metrics."""

from datetime import datetime, timedelta

import pytest

from vibe.trading_bot.brokers.base import BrokerOrder, FillEvent
from vibe.trading_bot.storage.metrics_store import MetricsStore
from vibe.trading_bot.storage.operational_metrics import OperationalMetricsRecorder


def test_broker_order_requires_positive_quantity():
    with pytest.raises(ValueError, match="quantity must be positive"):
        BrokerOrder(symbol="AAPL", side="buy", quantity=0)


def test_limit_order_requires_limit_price():
    with pytest.raises(ValueError, match="limit orders require limit_price"):
        BrokerOrder(symbol="AAPL", side="buy", quantity=1, order_type="limit")


def test_buy_fill_event_slippage_and_latency():
    submitted_at = datetime.utcnow()
    filled_at = submitted_at + timedelta(milliseconds=250)

    event = FillEvent(
        broker_order_id="1",
        symbol="AAPL",
        side="buy",
        quantity=1,
        avg_fill_price=101.0,
        expected_price=100.0,
        submitted_at=submitted_at,
        filled_at=filled_at,
    )

    assert event.slippage == 1.0
    assert event.slippage_bps == 100.0
    assert event.latency_ms == 250.0


def test_sell_fill_event_slippage_is_adverse_when_fill_below_expected():
    submitted_at = datetime.utcnow()
    event = FillEvent(
        broker_order_id="1",
        symbol="AAPL",
        side="sell",
        quantity=1,
        avg_fill_price=99.0,
        expected_price=100.0,
        submitted_at=submitted_at,
        filled_at=submitted_at,
    )

    assert event.slippage == 1.0
    assert event.slippage_bps == 100.0


@pytest.mark.asyncio
async def test_operational_metrics_recorder_records_fill_event(tmp_path):
    store = MetricsStore(str(tmp_path / "metrics.db"))
    recorder = OperationalMetricsRecorder(local_store=store)
    submitted_at = datetime.utcnow()
    event = FillEvent(
        broker_order_id="ib-1",
        symbol="AAPL",
        side="buy",
        quantity=2,
        avg_fill_price=101.0,
        expected_price=100.0,
        submitted_at=submitted_at,
        filled_at=submitted_at + timedelta(milliseconds=125),
        commission=0.5,
    )

    await recorder.record_fill_event(event)

    metric_names = {metric["metric_name"] for metric in store.get_metrics(metric_type="trade")}
    assert "expected_fill_price" in metric_names
    assert "actual_fill_price" in metric_names
    assert "slippage" in metric_names
    assert "slippage_bps" in metric_names
    assert "latency_ms" in metric_names
    assert "commission" in metric_names
