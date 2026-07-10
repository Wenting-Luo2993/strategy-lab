"""Unit tests for the Interactive Brokers execution adapter."""

from datetime import datetime

import pytest

from vibe.common.models import OrderStatus
from vibe.trading_bot.brokers.base import (
    BrokerAccount,
    BrokerPosition,
    BrokerQuote,
    FillEvent,
)
from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine


class FakeBroker:
    def __init__(self):
        self.orders = []
        self.cancelled = []

    async def connect(self):
        return True

    async def disconnect(self):
        return True

    async def get_market_data(self, symbol):
        return BrokerQuote(
            symbol=symbol,
            bid=99.95,
            ask=100.05,
            last=100.0,
            market_price=100.0,
        )

    async def submit_order(self, order):
        self.orders.append(order)
        return "ib-1"

    async def wait_for_fill(self, broker_order_id, timeout_seconds=60.0):
        return FillEvent(
            broker_order_id=broker_order_id,
            symbol="QQQ",
            side="buy",
            quantity=1,
            avg_fill_price=100.02,
            expected_price=100.0,
            submitted_at=datetime.now(),
            filled_at=datetime.now(),
            commission=1.0,
        )

    async def cancel_order(self, broker_order_id):
        self.cancelled.append(broker_order_id)
        return True

    async def get_order_status(self, broker_order_id):
        return {
            "broker_order_id": broker_order_id,
            "status": "Cancelled",
            "filled": 0.0,
            "remaining": 1.0,
            "avg_fill_price": 0.0,
        }

    async def get_account_info(self):
        return BrokerAccount(
            account_id="DU123",
            net_liquidation=10000.0,
            cash=9000.0,
            buying_power=30000.0,
        )

    async def get_positions(self):
        return [
            BrokerPosition(
                symbol="QQQ",
                quantity=1,
                avg_cost=99.5,
                market_price=100.25,
            )
        ]


@pytest.mark.asyncio
async def test_submit_order_maps_fill_and_caches_order():
    engine = InteractiveBrokersExecutionEngine(FakeBroker())

    response = await engine.submit_order("QQQ", "buy", 1, "market")
    order = await engine.get_order(response.order_id)

    assert response.order_id == "ib-1"
    assert response.status == OrderStatus.FILLED
    assert response.filled_qty == 1
    assert response.avg_price == 100.02
    assert order.status == OrderStatus.FILLED
    assert engine._prices["QQQ"] == 100.02


@pytest.mark.asyncio
async def test_account_and_position_mapping():
    engine = InteractiveBrokersExecutionEngine(FakeBroker())

    account = await engine.get_account()
    position = await engine.get_position("QQQ")

    assert account.equity == 10000.0
    assert account.cash == 9000.0
    assert account.buying_power == 30000.0
    assert position.symbol == "QQQ"
    assert position.side == "long"
    assert position.quantity == 1
    assert position.entry_price == 99.5
    assert position.current_price == 100.25


@pytest.mark.asyncio
async def test_cancel_unknown_order_raises():
    engine = InteractiveBrokersExecutionEngine(FakeBroker())

    with pytest.raises(ValueError, match="Unknown order"):
        await engine.cancel_order("missing")