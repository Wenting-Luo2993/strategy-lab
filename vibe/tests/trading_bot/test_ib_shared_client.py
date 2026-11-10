"""Behavioral coverage for concurrent consumers of the single IB client."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import vibe.trading_bot.brokers.interactive_brokers as ib_module
from vibe.trading_bot.data.providers.factory import DataProviderFactory
from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

PRODUCTION_TICK_INTERVAL = ib_module._TICK_BY_TICK_REQUEST_INTERVAL_SECONDS


class EventHook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self):
        for handler in self.handlers:
            handler()


class ControlledIB:
    def __init__(self):
        self.errorEvent = EventHook()
        self.disconnectedEvent = EventHook()
        self.connected = True
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.connect_started = asyncio.Event()
        self.connect_gate = asyncio.Event()
        self.connect_gate.set()
        self.started = asyncio.Queue()
        self.active = set()
        self.tickers = {}
        self.events = []
        self.request_times = []
        self.fail_tick_subscription = False

    def isConnected(self):
        return self.connected

    async def connectAsync(self, *args, **kwargs):
        self.connect_calls += 1
        self.connect_started.set()
        await self.connect_gate.wait()
        self.connected = True

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False
        self.disconnectedEvent.emit()

    def openTrades(self):
        return []

    async def reqExecutionsAsync(self):
        return []

    async def qualifyContractsAsync(self, contract):
        contract.conId = {"QQQ": 1, "MSFT": 2}[contract.symbol]
        return [contract]

    def reqMarketDataType(self, market_data_type):
        assert market_data_type == 1

    def reqMktData(self, contract, *args):
        assert contract.symbol not in self.active, "Overlapping subscription!"
        self.active.add(contract.symbol)
        ticker = SimpleNamespace(last=100.0, tickByTicks=[])
        self.tickers[contract.symbol] = ticker
        self.events.append(("start", contract.symbol))
        return ticker

    def reqTickByTickData(self, contract, *args):
        self.request_times.append(asyncio.get_running_loop().time())
        if self.fail_tick_subscription:
            raise RuntimeError("Tick subscription rejected")
        self.started.put_nowait(contract.symbol)
        return self.tickers[contract.symbol]

    def cancelMktData(self, contract):
        self.active.remove(contract.symbol)
        self.events.append(("cancel_market", contract.symbol))

    def cancelTickByTickData(self, contract, *args):
        self.events.append(("cancel_ticks", contract.symbol))

    def emit(self, symbol):
        timestamp = datetime.now(timezone.utc)
        self.tickers[symbol].tickByTicks.append(
            SimpleNamespace(time=timestamp, bidPrice=99.0, askPrice=101.0)
        )
        return timestamp


@pytest.fixture
def shared_broker(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ib = ControlledIB()
    monkeypatch.setattr(ib_module, "IB", lambda: ib)
    monkeypatch.setattr(ib_module, "_TICK_BY_TICK_REQUEST_INTERVAL_SECONDS", 0.0)
    broker = ib_module.InteractiveBrokersAPI(
        client_id=201, execution_db_path=str(tmp_path / "executions.sqlite3")
    )
    return broker, ib


async def started(ib, symbol):
    assert await asyncio.wait_for(ib.started.get(), timeout=1) == symbol


async def cancelled(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_factory_borrows_identical_writable_broker_for_both_providers(shared_broker):
    broker, ib = shared_broker
    primary = DataProviderFactory.create_realtime_provider("ib", ib_broker=broker)
    secondary = DataProviderFactory.create_realtime_provider("ibkr", ib_broker=broker)
    engine = InteractiveBrokersExecutionEngine(broker)

    assert primary.broker is secondary.broker is engine.broker
    assert primary.broker.ib is ib
    assert broker.client_id == 201
    assert broker.readonly is False
    assert await primary.connect()
    assert await secondary.connect()
    await primary.disconnect()
    assert not primary.connected
    assert secondary.connected
    assert ib.connect_calls == ib.disconnect_calls == 0
    await secondary.disconnect()
    assert ib.isConnected()
    await engine.close()
    assert not ib.isConnected()
    assert ib.disconnect_calls == 1


@pytest.mark.asyncio
async def test_factory_standalone_provider_owns_readonly_connection(shared_broker):
    _, ib = shared_broker
    ib.connected = False
    provider = DataProviderFactory.create_realtime_provider("ib", ib_client_id=202)
    assert provider.broker.readonly
    assert provider.broker.client_id == 202
    assert await provider.connect()
    assert ib.connect_calls == 1
    await provider.disconnect()
    assert ib.disconnect_calls == 1
    assert not ib.isConnected()


@pytest.mark.asyncio
async def test_same_contract_provider_and_execution_share_one_live_request(shared_broker):
    broker, ib = shared_broker
    provider = DataProviderFactory.create_realtime_provider("ib", ib_broker=broker)
    first = asyncio.create_task(provider.get_latest_bar("QQQ"))
    await started(ib, "QQQ")
    second = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await asyncio.sleep(0.01)
    assert ib.events == [("start", "QQQ")]

    first_time = ib.emit("QQQ")
    bar = await asyncio.wait_for(first, timeout=1)
    assert bar["timestamp"] == first_time
    quote = await asyncio.wait_for(second, timeout=1)
    assert quote.timestamp == first_time
    assert quote.market_price == 100.0
    assert ib.events == [
        ("start", "QQQ"), ("cancel_market", "QQQ"), ("cancel_ticks", "QQQ"),
    ]
    assert not broker._quote_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_waiter_timeout_or_cancellation_does_not_cancel_owner(
    shared_broker, cancel_waiter
):
    broker, ib = shared_broker
    owner = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=2))
    await started(ib, "QQQ")
    waiter = asyncio.create_task(
        broker.get_market_data("QQQ", timeout_seconds=2 if cancel_waiter else 0.02)
    )
    if cancel_waiter:
        await asyncio.sleep(0.01)
        await cancelled(waiter)
    else:
        with pytest.raises(TimeoutError, match="timestamped live market data"):
            await asyncio.wait_for(waiter, timeout=0.5)
    assert ib.events == [("start", "QQQ")]
    timestamp = ib.emit("QQQ")
    assert (await asyncio.wait_for(owner, timeout=1)).timestamp == timestamp
    assert not ib.active


@pytest.mark.asyncio
async def test_cancelling_first_caller_keeps_second_callers_subscription(shared_broker):
    broker, ib = shared_broker
    owner = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=2))
    await started(ib, "QQQ")
    waiter = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=2))
    await asyncio.sleep(0.01)
    await cancelled(owner)
    assert ib.events == [("start", "QQQ")]
    timestamp = ib.emit("QQQ")
    assert (await asyncio.wait_for(waiter, timeout=1)).timestamp == timestamp
    assert ib.events.count(("cancel_market", "QQQ")) == 1
    assert ib.events.count(("cancel_ticks", "QQQ")) == 1
    assert not broker._quote_requests


@pytest.mark.asyncio
async def test_last_caller_cancel_cleans_up_before_next_request(shared_broker):
    broker, ib = shared_broker
    owner = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=2))
    await started(ib, "QQQ")
    await cancelled(owner)
    assert not ib.active
    assert not broker._quote_requests
    retry = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    ib.emit("QQQ")
    await asyncio.wait_for(retry, timeout=1)
    assert ib.events.count(("cancel_market", "QQQ")) == 2


@pytest.mark.asyncio
async def test_completed_quote_is_not_cached_and_next_request_obeys_ib_pacing(
    shared_broker, monkeypatch
):
    broker, ib = shared_broker
    monkeypatch.setattr(ib_module, "_TICK_BY_TICK_REQUEST_INTERVAL_SECONDS", 0.2)
    first = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    first_time = ib.emit("QQQ")
    assert (await asyncio.wait_for(first, 1)).timestamp == first_time
    with pytest.raises(TimeoutError):
        await broker.get_market_data("QQQ", timeout_seconds=0.02)
    assert len(ib.request_times) == 1
    assert not broker._quote_requests
    second = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    second_time = ib.emit("QQQ")
    assert (await asyncio.wait_for(second, 1)).timestamp == second_time
    assert ib.request_times[1] - ib.request_times[0] >= 0.2


@pytest.mark.asyncio
async def test_different_contracts_can_receive_quotes_concurrently(shared_broker):
    broker, ib = shared_broker
    qqq = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=2))
    await started(ib, "QQQ")
    msft = asyncio.create_task(broker.get_market_data("MSFT", timeout_seconds=1))
    await started(ib, "MSFT")
    assert ib.active == {"QQQ", "MSFT"}
    ib.emit("MSFT")
    assert (await asyncio.wait_for(msft, timeout=1)).symbol == "MSFT"
    assert ib.active == {"QQQ"}
    ib.emit("QQQ")
    await asyncio.wait_for(qqq, timeout=1)


@pytest.mark.asyncio
async def test_gap_quote_timeout_and_provider_cooldown_leave_execution_quote_alive(
    shared_broker
):
    broker, ib = shared_broker
    provider = DataProviderFactory.create_realtime_provider("ib", ib_broker=broker)
    engine = InteractiveBrokersExecutionEngine(broker)
    assert await provider.connect()
    execution_quote = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=2))
    await started(ib, "QQQ")
    contract = await broker.qualify_contract("QQQ")
    price = await asyncio.wait_for(
        engine._fresh_reference_price("QQQ", contract, timeout_s=0.02), timeout=0.5
    )
    assert price is None
    await provider.disconnect()
    assert ib.isConnected()
    assert ib.events == [("start", "QQQ")]
    ib.emit("QQQ")
    await asyncio.wait_for(execution_quote, timeout=1)
    assert ib.disconnect_calls == 0


@pytest.mark.asyncio
async def test_quote_timeout_includes_contract_qualification(shared_broker):
    broker, ib = shared_broker
    gate = asyncio.Event()

    async def qualify(_symbol):
        await gate.wait()

    broker.qualify_contract = AsyncMock(side_effect=qualify)
    with pytest.raises(TimeoutError, match="timestamped live market data"):
        await asyncio.wait_for(broker.get_market_data("QQQ", timeout_seconds=0.02), 0.5)
    assert not ib.events


@pytest.mark.asyncio
async def test_partial_subscription_failure_cleans_up_and_releases_lock(shared_broker):
    broker, ib = shared_broker
    ib.fail_tick_subscription = True
    with pytest.raises(RuntimeError, match="Tick subscription rejected"):
        await broker.get_market_data("QQQ")
    assert ib.events == [("start", "QQQ"), ("cancel_market", "QQQ")]
    ib.fail_tick_subscription = False
    retry = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    ib.emit("QQQ")
    await asyncio.wait_for(retry, timeout=1)
    assert not ib.active


@pytest.mark.asyncio
async def test_concurrent_recovery_then_shutdown_serializes_transport(shared_broker):
    broker, ib = shared_broker
    ib.connected = False
    ib.connect_gate.clear()
    first = asyncio.create_task(broker.connect())
    await asyncio.wait_for(ib.connect_started.wait(), 1)
    second = asyncio.create_task(broker.connect())
    await asyncio.sleep(0)
    closing = asyncio.create_task(broker.disconnect())
    await asyncio.sleep(0)
    assert ib.connect_calls == 1
    assert not closing.done()
    ib.connect_gate.set()
    assert await asyncio.wait_for(asyncio.gather(first, second, closing), 1) == [
        True, True, True
    ]
    assert ib.connect_calls == 1
    assert ib.disconnect_calls == 1
    assert not ib.isConnected()


@pytest.mark.asyncio
async def test_cancelled_recovery_releases_connection_lock(shared_broker):
    broker, ib = shared_broker
    ib.connected = False
    ib.connect_gate.clear()
    recovery = asyncio.create_task(broker.connect())
    await asyncio.wait_for(ib.connect_started.wait(), 1)
    await cancelled(recovery)
    assert not ib.isConnected()
    ib.connect_gate.set()
    assert await asyncio.wait_for(broker.connect(), 1)
    assert ib.connect_calls == 2


@pytest.mark.asyncio
async def test_socket_loss_rejects_old_connection_quote_then_new_request_succeeds(
    shared_broker
):
    broker, ib = shared_broker
    old_quote = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    ib.emit("QQQ")
    ib.disconnect()
    assert await broker.connect()
    with pytest.raises(ConnectionError, match="connection changed"):
        await asyncio.wait_for(old_quote, 1)
    assert not ib.active
    fresh_quote = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    timestamp = ib.emit("QQQ")
    assert (await asyncio.wait_for(fresh_quote, 1)).timestamp == timestamp
    assert broker.ib is ib


@pytest.mark.asyncio
async def test_production_tick_pacing_waits_full_fifteen_seconds(shared_broker, monkeypatch):
    broker, ib = shared_broker
    assert PRODUCTION_TICK_INTERVAL == 15.0
    monkeypatch.setattr(
        ib_module, "_TICK_BY_TICK_REQUEST_INTERVAL_SECONDS", PRODUCTION_TICK_INTERVAL
    )
    first = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    ib.emit("QQQ")
    await asyncio.wait_for(first, 1)
    second = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=17))
    assert await asyncio.wait_for(ib.started.get(), 16) == "QQQ"
    ib.emit("QQQ")
    await asyncio.wait_for(second, 1)
    assert ib.request_times[1] - ib.request_times[0] >= 15.0


@pytest.mark.asyncio
async def test_cancellation_cleanup_failure_is_logged_and_tick_subscription_cancelled(
    shared_broker, caplog
):
    broker, ib = shared_broker

    def fail_cancel(_contract):
        raise RuntimeError("Cancel market data failed")

    ib.cancelMktData = fail_cancel
    quote = asyncio.create_task(broker.get_market_data("QQQ", timeout_seconds=1))
    await started(ib, "QQQ")
    await cancelled(quote)
    assert ("cancel_ticks", "QQQ") in ib.events
    assert "Failed to clean up IB quote request" in caplog.text
    assert "Cancel market data failed" in caplog.text
    assert not broker._quote_requests
