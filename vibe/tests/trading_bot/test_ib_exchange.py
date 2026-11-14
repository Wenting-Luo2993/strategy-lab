"""Unit tests for the Interactive Brokers execution adapter."""

from datetime import datetime, timezone
from dataclasses import replace
import logging
from types import SimpleNamespace

import pytest

from vibe.common.models import OrderStatus
from vibe.trading_bot.brokers.base import (
    BrokerAccount,
    BrokerOrder,
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
            timestamp=datetime.now(timezone.utc),
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
                instrument_currency="CAD",
                unrealized_pnl_currency="CAD",
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
    assert position.instrument_currency == "CAD"
    assert position.unrealized_pnl_currency == "CAD"


@pytest.mark.asyncio
async def test_locally_recalculated_position_pnl_uses_instrument_currency():
    class CadAccountUsdInstrumentBroker(FakeBroker):
        async def get_account_info(self):
            return BrokerAccount(
                account_id="DU-CAD",
                net_liquidation=10000,
                cash=9000,
                buying_power=20000,
                currency="CAD",
                unrealized_pnl=13.5,
                unrealized_pnl_currency="CAD",
            )

        async def get_positions(self):
            return [
                BrokerPosition(
                    symbol="QQQ",
                    quantity=10,
                    avg_cost=100,
                    market_price=101,
                    unrealized_pnl=13.5,
                    instrument_currency="USD",
                    unrealized_pnl_currency="CAD",
                )
            ]

    engine = InteractiveBrokersExecutionEngine(
        CadAccountUsdInstrumentBroker()
    )

    broker_snapshot = await engine.get_position_snapshot("QQQ")
    local_position = await engine.get_position("QQQ")

    assert broker_snapshot.unrealized_pnl == 13.5
    assert broker_snapshot.unrealized_pnl_currency == "CAD"
    assert local_position.unrealized_pnl == 10
    assert local_position.unrealized_pnl_currency == "USD"


@pytest.mark.asyncio
async def test_cancel_unknown_order_raises():
    engine = InteractiveBrokersExecutionEngine(FakeBroker())

    with pytest.raises(ValueError, match="Unknown order"):
        await engine.cancel_order("missing")


@pytest.mark.asyncio
async def test_initialize_blocks_duplicate_symbol_for_restored_open_ib_order():
    class BrokerWithOpenOrder(FakeBroker):
        def list_open_orders(self):
            return [{
                "order": BrokerOrder(
                    symbol="QQQ",
                    side="buy",
                    quantity=1,
                    broker_order_id="existing",
                    status="submitted",
                ),
                "filled_qty": 0.0,
                "avg_price": 0.0,
            }]

    engine = InteractiveBrokersExecutionEngine(BrokerWithOpenOrder())
    await engine.initialize()

    restored = engine.list_open_orders()
    assert len(restored) == 1
    assert restored[0].order_id == "existing"
    assert restored[0].status == OrderStatus.SUBMITTED
    with pytest.raises(RuntimeError, match="refusing duplicate"):
        await engine.submit_order("QQQ", "buy", 1, "market")


class ReusedOrderIdBroker(FakeBroker):
    """Friday's QQQ169 -> TSLA169 collision, with a broker-authoritative stop."""

    def __init__(self, old_symbol="QQQ", cancel_fill=0, acknowledge=True):
        super().__init__()
        self.events = []
        self.position = -4
        self.cancel_fill = cancel_fill
        self.acknowledge = acknowledge
        self.stop_open = True
        self.stop = BrokerOrder(
            symbol="TSLA", side="buy", quantity=4, broker_order_id="169",
            order_identity="ib:DU123:1743199735", permanent_order_id="1743199735",
            account_id="DU123", order_type="stop", stop_price=404.4,
            order_role="protective_stop", parent_order_id="168",
        )
        self.history = [{
            "execution_id": "qqq-stop", "broker_order_id": "169",
            "permanent_order_id": "974881495", "account_id": "DU123",
            "symbol": old_symbol, "side": "buy", "quantity": 2, "price": 601,
            "filled_at": datetime(2026, 11, 11, 15, 43, tzinfo=timezone.utc),
            "order_metadata": {"quantity": 2, "order_role": "protective_stop"},
        }]
        self.submitted = {"169": self.stop}

    def list_durable_executions(self):
        return self.history

    def list_open_orders(self):
        return [{"order": self.stop, "filled_qty": 0}] if self.stop_open else []

    def get_submitted_order(self, api_id):
        return self.submitted.get(api_id)

    async def get_positions(self):
        return [BrokerPosition("TSLA", self.position, 396.02, market_price=392.4)] if self.position else []

    async def cancel_order(self, api_id):
        self.events.append(("cancel", api_id))
        if not self.acknowledge:
            return False
        self.stop_open = False
        if self.cancel_fill:
            self.position += self.cancel_fill
            self.history.append({
                "execution_id": "tsla-stop", "broker_order_id": "169",
                "permanent_order_id": "1743199735", "account_id": "DU123",
                "symbol": "TSLA", "side": "buy", "quantity": self.cancel_fill,
                "price": 404.4, "filled_at": datetime.now(timezone.utc),
                "order_metadata": {"quantity": 4},
            })
        return True

    async def get_order_status(self, api_id):
        return {
            "status": "Filled" if self.cancel_fill == 4 else "Cancelled",
            "filled": self.cancel_fill, "avg_fill_price": 404.4 if self.cancel_fill else 0,
        }

    async def submit_order(self, order):
        assert not self.stop_open
        self.events.append(("close", order.side, order.quantity))
        self.orders.append(order)
        self.submitted["170"] = replace(order, broker_order_id="170", order_identity="sl:close")
        return "170"

    async def wait_for_fill(self, api_id, timeout_seconds=60):
        order = self.submitted[api_id]
        self.position += order.quantity
        now = datetime.now(timezone.utc)
        return FillEvent(
            broker_order_id=api_id, symbol="TSLA", side="buy", quantity=order.quantity,
            avg_fill_price=392.4, expected_price=392.4, submitted_at=now, filled_at=now,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("old_symbol", ["QQQ", "TSLA"])
@pytest.mark.parametrize("cancel_fill", [0, 2, 4])
@pytest.mark.parametrize("has_verified_role", [True, False])
async def test_reused_order_id_restores_stop_and_closes_only_remaining_position(old_symbol, cancel_fill, has_verified_role):
    from vibe.common.risk import PositionSizer
    from vibe.trading_bot.execution.order_manager import OrderManager
    from vibe.trading_bot.execution.trade_executor import TradeExecutor, SimpleRiskManager
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator

    broker = ReusedOrderIdBroker(old_symbol, cancel_fill)
    if not has_verified_role:
        broker.stop = replace(broker.stop, order_role=None)
        broker.submitted["169"] = broker.stop
    engine = InteractiveBrokersExecutionEngine(broker)
    await engine.initialize()
    (stop,) = engine.list_open_orders()
    assert (stop.symbol, stop.quantity, stop.filled_qty) == ("TSLA", 4, 0)
    assert stop.permanent_order_id == "1743199735"
    assert (await engine.get_order("ib:DU123:974881495")).filled_qty == 2
    await TradingOrchestrator._adopt_restored_protective_stops(
        SimpleNamespace(exchange=engine, logger=logging.getLogger(__name__)), [stop],
    )
    manager = OrderManager(exchange=engine)
    sizer = PositionSizer(risk_pct=0.01)
    executor = TradeExecutor(engine, manager, sizer, SimpleRiskManager(sizer))
    manager.restore_open_orders([stop])
    executor.restore_pending_entries([stop])
    try:
        assert executor.has_protective_stop("TSLA")
        # Even a late old callback must not clear the restored-order guard.
        engine._on_broker_execution(broker.history[0])
        with pytest.raises(RuntimeError, match="refusing duplicate"):
            await engine.submit_order("TSLA", "buy", 4, "market")
        await executor._close_position("TSLA", exit_reason="EOD")
        assert broker.events[0] == ("cancel", "169")
        if cancel_fill < 4:
            assert broker.events == [("cancel", "169"), ("close", "buy", 4 - cancel_fill)]
        else:
            assert broker.events == [("cancel", "169")]
        assert broker.position == 0
        assert stop.filled_qty == cancel_fill
        assert (await engine.get_order("ib:DU123:974881495")).filled_qty == 2
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_cancellation_cannot_route_to_reused_order_number():
    broker = ReusedOrderIdBroker()
    engine = InteractiveBrokersExecutionEngine(broker)
    await engine.initialize()
    broker.submitted["169"] = replace(broker.stop, order_identity="sl:another-lifetime")
    with pytest.raises(RuntimeError, match="another order lifetime"):
        await engine.cancel_order("ib:DU123:1743199735")
    assert not broker.events


@pytest.mark.asyncio
async def test_unconfirmed_cancel_does_not_submit_close():
    broker = ReusedOrderIdBroker(acknowledge=False)
    engine = InteractiveBrokersExecutionEngine(broker)
    await engine.initialize()
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        await engine.cancel_order("ib:DU123:1743199735")
    assert broker.events == [("cancel", "169")]
    assert broker.position == -4 and broker.stop_open


@pytest.mark.asyncio
async def test_current_partial_fill_and_correction_remain_in_their_lifetime():
    broker = ReusedOrderIdBroker()
    current = {
        **broker.history[0], "execution_id": "current.aa.01.01",
        "permanent_order_id": "1743199735", "symbol": "TSLA",
        "quantity": 1, "price": 404.4,
        "order_metadata": {"quantity": 4, "order_role": "protective_stop"},
    }
    broker.history.append(current)
    engine = InteractiveBrokersExecutionEngine(broker)
    await engine.initialize()
    (stop,) = engine.list_open_orders()
    assert stop.status == OrderStatus.PARTIAL and stop.filled_qty == 1
    assert (stop.order_type, stop.order_role, stop.stop_price) == ("stop", "protective_stop", 404.4)
    engine._on_broker_execution({**current, "execution_id": "current.aa.01.02", "price": 404.3})
    engine._on_broker_execution(current)
    assert stop.filled_qty == 1 and stop.avg_price == 404.3
    assert len(stop.executions) == 1
    assert (await engine.get_order("ib:DU123:974881495")).filled_qty == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [0, 4])
async def test_unidentified_live_stop_cannot_become_an_entry(position):
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator

    broker = ReusedOrderIdBroker()
    broker.position = position
    broker.stop = replace(broker.stop, order_role=None)
    engine = InteractiveBrokersExecutionEngine(broker)
    await engine.initialize()
    with pytest.raises(RuntimeError, match="reconciliation required"):
        await TradingOrchestrator._adopt_restored_protective_stops(
            SimpleNamespace(exchange=engine, logger=logging.getLogger(__name__)),
            engine.list_open_orders(),
        )
    assert not broker.events