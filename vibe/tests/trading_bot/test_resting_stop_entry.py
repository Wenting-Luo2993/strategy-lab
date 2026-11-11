"""Stage 3: resting-stop OCA entries with broker-side protective stops.

Covers OCA linkage and bracket construction (IB adapter), gap fills, the
entry-cutoff cancel, the EOD race with a filled protective stop, restart
recovery without duplicate orders, no client-side double exit, fill
telemetry, and reconciling broker executions before a late E0.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from vibe.common.execution.base import PartialPlacementError
import yaml

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.common.execution.base import RestingEntryLeg
from vibe.common.models import Order, OrderStatus
from vibe.common.risk import PositionSizer
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.models import EntryOrderPolicy, PortfolioConfig, StrategyRuleSet
from vibe.common.ruleset.support import UnsupportedRulesetSettingsError, unsupported_settings
from vibe.trading_bot.brokers.base import BrokerOrder
from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.trading_bot.core.phases.warmup import WarmupPhaseManager
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import OrderManager
from vibe.trading_bot.execution.portfolio_risk import (
    PortfolioRiskManager,
    SessionEquitySnapshot,
)
from vibe.trading_bot.execution.trade_executor import TradeExecutor
from vibe.trading_bot.storage.dashboard_store import OrderEvent

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 7, 20)


def _mock() -> MockExchange:
    return MockExchange(initial_capital=100_000.0, commission_pct=0.0, partial_fill_probability=0.0)


def _risk_manager() -> PortfolioRiskManager:
    manager = PortfolioRiskManager(
        PortfolioConfig(
            sizing_equity_basis="session_start",
            max_symbol_exposure_pct=0.20,
            max_gross_leverage=1.0,
            max_open_positions=5,
            daily_loss_limit_pct=0.03,
        ),
        PositionSizer(risk_pct=0.01),
    )
    manager.record_snapshot(
        SessionEquitySnapshot(SESSION, 100_000.0, datetime(2026, 7, 20, 9, 25, tzinfo=ET))
    )
    return manager


def _executor(exchange, manager=None, **callbacks) -> TradeExecutor:
    return TradeExecutor(
        exchange=exchange,
        order_manager=OrderManager(exchange=exchange, **callbacks),
        position_sizer=PositionSizer(risk_pct=0.01),
        risk_manager=manager or _risk_manager(),
    )


async def _place(executor, cancel_after=3600.0, **levels):
    params = dict(long_level=101.0, short_level=99.0, long_stop=99.0, short_stop=101.0)
    params.update(levels)
    return await executor.place_resting_stop_entries(
        "AAA", cancel_after_seconds=cancel_after, strategy_name="ORB", **params
    )


def _orders(exchange, role=None):
    return [
        order for order in exchange.get_all_orders().values()
        if role is None or order.order_role == role
    ]


# ---------------------------------------------------------------------------
# Ruleset and schema
# ---------------------------------------------------------------------------

def test_paper_ruleset_is_the_row10_definition():
    ruleset = RuleSetLoader.from_name("orb_resting_stop_paper")
    assert ruleset.strategy.entry_order.type == "resting_stop"
    assert ruleset.strategy.orb_duration_minutes == 5
    assert ruleset.strategy.entry_cutoff_time == "15:00"
    assert ruleset.exit.eod_time == "15:55"
    assert ruleset.exit.take_profit.multiplier == 0
    assert ruleset.instruments.symbols == ["QQQ"]
    assert ruleset.portfolio.max_symbol_exposure_pct == 0.20
    assert unsupported_settings(ruleset, "live") == []
    assert unsupported_settings(ruleset, "backtest") == []


def test_resting_stop_policy_rejects_expiry_offsets_and_extra_attempts():
    for bad in ({"expiry_bars": 3}, {"offset_ticks": 1}, {"max_attempts_per_day": 2}):
        with pytest.raises(ValueError):
            EntryOrderPolicy(type="resting_stop", **bad)


def test_resting_stop_live_rejects_take_profit_and_trailing():
    ruleset = RuleSetLoader.from_name("orb_resting_stop_paper")
    data = ruleset.model_dump()
    data["exit"]["take_profit"] = {"method": "orb_range_multiple", "multiplier": 2.0}
    data["exit"]["trailing_stop"] = {"method": "breakeven_plus_ticks", "trigger_r": 3.0}
    problems = unsupported_settings(StrategyRuleSet.model_validate(data), "live")
    assert any("take_profit" in p for p in problems)
    assert any("trailing_stop" in p for p in problems)


def test_backtest_requires_the_resting_stop_fill_policy(tmp_path):
    ruleset = RuleSetLoader.from_name("orb_resting_stop_paper")
    with pytest.raises(UnsupportedRulesetSettingsError, match="RESTING_STOP"):
        BacktestEngine(ruleset, data_dir=tmp_path, execution_realism=ExecutionRealismConfig.realistic())
    BacktestEngine(
        ruleset, data_dir=tmp_path,
        execution_realism=ExecutionRealismConfig.realistic_resting_stop(),
    )


# ---------------------------------------------------------------------------
# OCA linkage and brackets (mock exchange)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oca_pair_rests_both_sides_with_held_protective_stops():
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)

    result = await _place(executor)

    assert result.pending and result.order_id
    entries = _orders(exchange, "entry")
    stops = _orders(exchange, "protective_stop")
    assert sorted((e.side, e.stop_price) for e in entries) == [("buy", 101.0), ("sell", 99.0)]
    assert len({e.oca_group for e in entries}) == 1
    assert all(e.gap_fill is False for e in entries)
    # Each entry carries its own stop at the opposite edge, held until it fills.
    by_parent = {s.parent_order_id: s for s in stops}
    for entry in entries:
        stop = by_parent[entry.order_id]
        assert stop.side != entry.side
        assert stop.stop_price == (99.0 if entry.side == "buy" else 101.0)
        assert stop.status == OrderStatus.SUBMITTED
    # The pair reserves the larger side once (both sides are capped at 20%).
    reserved = executor.pending_exposures({})
    assert {p.group for p in reserved} == {entries[0].oca_group}


@pytest.mark.asyncio
async def test_first_fill_cancels_the_other_side_and_activates_its_stop():
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    await _place(executor)

    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()

    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    sell = next(o for o in _orders(exchange, "entry") if o.side == "sell")
    stops = {s.parent_order_id: s for s in _orders(exchange, "protective_stop")}
    assert buy.status == OrderStatus.FILLED
    assert sell.status == OrderStatus.CANCELLED
    assert stops[sell.order_id].status == OrderStatus.CANCELLED
    assert stops[buy.order_id].status == OrderStatus.PENDING  # live at the broker

    executor.mark_entry_filled(buy.order_id)
    assert executor.has_protective_stop("AAA")
    assert executor.protective_stop_order_id("AAA") == stops[buy.order_id].order_id
    assert not any(c["side"] == "sell" for c in executor._pending_entries.values())

    await exchange.set_price("AAA", 98.9)
    await exchange.process_pending_orders()
    assert stops[buy.order_id].status == OrderStatus.FILLED
    assert await exchange.get_position("AAA") is None


@pytest.mark.asyncio
async def test_level_already_traded_through_fills_immediately_as_gap_fill():
    exchange = _mock()
    await exchange.set_price("AAA", 101.6)
    executor = _executor(exchange)

    await _place(executor)

    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    sell = next(o for o in _orders(exchange, "entry") if o.side == "sell")
    assert buy.gap_fill is True and sell.gap_fill is False
    assert buy.status == OrderStatus.FILLED
    assert sell.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_unfilled_entries_are_cancelled_at_the_entry_cutoff():
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    cancelled = []

    async def on_cancelled(order_id):
        cancelled.append(order_id)

    executor = _executor(exchange, on_order_cancelled=on_cancelled)
    await _place(executor, cancel_after=0.05)

    for _ in range(40):
        await asyncio.sleep(0.1)
        if all(o.status == OrderStatus.CANCELLED for o in _orders(exchange)):
            break

    assert all(o.status == OrderStatus.CANCELLED for o in _orders(exchange, "entry"))
    # The attached stops go with their unfilled parents.
    assert all(o.status == OrderStatus.CANCELLED for o in _orders(exchange, "protective_stop"))
    assert {o.order_id for o in _orders(exchange, "entry")} <= set(cancelled)
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_eod_close_cancels_the_protective_stop_before_the_market_order():
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    stop_id = executor.protective_stop_order_id("AAA")

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert result.success and result.fully_closed
    assert (await exchange.get_order(stop_id)).status == OrderStatus.CANCELLED
    assert not executor.has_protective_stop("AAA")
    assert await exchange.get_position("AAA") is None
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_unconfirmed_protection_cancel_alerts_once_and_never_sends_close(monkeypatch):
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    stop_id = executor.protective_stop_order_id("AAA")
    alerts = []

    async def alert(title, message):
        alerts.append((title, message))

    async def failed_cancel(order_id):
        raise TimeoutError("No cancellation acknowledgement")

    executor.on_alert = alert
    monkeypatch.setattr(executor.order_manager, "cancel_order", failed_cancel)
    before = len(exchange.get_all_orders())
    for _ in range(2):
        result = await executor._close_position("AAA", exit_reason="time_exit")
        assert not result.success and not result.order_id
    assert len(alerts) == 1
    assert "No market close was sent" in alerts[0][1]
    assert len(exchange.get_all_orders()) == before
    assert executor.protective_stop_order_id("AAA") == stop_id
    assert await exchange.get_position("AAA") is not None
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_eod_race_with_a_filled_protective_stop_sends_no_market_order():
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    # The stop fills at the broker just before the EOD close runs.
    await exchange.set_price("AAA", 98.9)
    await exchange.process_pending_orders()
    orders_before = len(exchange.get_all_orders())

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert not result.success
    assert "protective stop" in result.reason
    assert len(exchange.get_all_orders()) == orders_before
    assert await exchange.get_position("AAA") is None
    await executor.order_manager.shutdown()


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------

def _restored(order_id, side, role, price, *, parent=None, oca="ORB-AAA-x", qty=50.0):
    return Order(
        order_id=order_id, symbol="AAA", side=side, quantity=qty, price=price,
        order_type="stop", status=OrderStatus.SUBMITTED, stop_price=price,
        strategy_stop_price=99.0 if side == "buy" else 101.0,
        order_role=role, oca_group=oca if role == "entry" else None,
        parent_order_id=parent,
        entry_signal_bar_timestamp=datetime(2026, 7, 20, 9, 35, tzinfo=ET),
    )


def test_restart_rebuilds_resting_pairs_and_live_protective_stops():
    executor = _executor(_mock())
    open_orders = [
        _restored("11", "buy", "entry", 101.0),
        _restored("12", "sell", "protective_stop", 99.0, parent="11"),
        _restored("13", "sell", "entry", 99.0),
        _restored("14", "buy", "protective_stop", 101.0, parent="13"),
    ]

    assert executor.restore_pending_entries(open_orders) == 2

    assert {c["group"] for c in executor._pending_entries.values()} == {"ORB-AAA-x"}
    assert executor.get_pending_entry("11")["protective_order_id"] == "12"
    assert not executor.has_protective_stop("AAA")
    # Only one side can fill, so the restored pair reserves one side.
    assert len({p.group for p in executor.pending_exposures({})}) == 1


def test_restart_with_a_filled_entry_keeps_its_stop_protecting_the_position():
    executor = _executor(_mock())
    open_orders = [_restored("12", "sell", "protective_stop", 99.0, parent="11")]

    assert executor.restore_pending_entries(open_orders) == 0

    assert executor.has_protective_stop("AAA")
    assert executor.protective_stop_order_id("AAA") == "12"


@pytest.mark.asyncio
async def test_restored_protective_stop_is_never_timed_out():
    exchange = _mock()
    manager = OrderManager(exchange=exchange)
    stop = _restored("12", "sell", "protective_stop", 99.0, parent="11")
    stop.entry_cancel_after_seconds = None
    exchange._orders["12"] = SimpleNamespace(order=stop)  # broker-side stop

    manager.restore_open_orders([stop])
    managed = manager.get_order("12")
    assert managed.persistent and managed.linked
    await manager.shutdown()


# ---------------------------------------------------------------------------
# IB adapter: OCA bracket construction and duplicate protection
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.next_id = 100

    def getReqId(self):
        self.next_id += 1
        return self.next_id


class _FakeIB:
    def __init__(self):
        self.client = _FakeClient()
        self.placed = []

    def placeOrder(self, contract, order):
        if not order.orderId:
            order.orderId = self.client.getReqId()
        self.placed.append(order)
        return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status="PreSubmitted"))

    def cancelOrder(self, order):
        self.cancelled = getattr(self, "cancelled", []) + [order.orderId]


def _ib_api(tmp_path):
    from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI

    api = InteractiveBrokersAPI.__new__(InteractiveBrokersAPI)
    from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore

    api.ib = _FakeIB()
    api.readonly = False
    api.account_id = "DU1"
    api._trades = {}
    api._submitted_orders = {}
    api.execution_store = IBExecutionStore(str(tmp_path / "ib.db"))
    return api


@pytest.mark.asyncio
async def test_ib_pending_cancel_is_not_resent_and_waits_for_confirmation(tmp_path):
    api = _ib_api(tmp_path)
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=101),
        orderStatus=SimpleNamespace(status="PendingCancel"), fills=[], log=[],
    )
    api._trades["101"] = trade
    with pytest.raises(TimeoutError):
        await api.cancel_order("101", timeout_seconds=0.1)
    assert not getattr(api.ib, "cancelled", [])
    trade.orderStatus.status = "Cancelled"
    assert await api.cancel_order("101", timeout_seconds=0.1)
    assert not getattr(api.ib, "cancelled", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [10147, 10148])
async def test_ib_cancel_error_is_not_a_terminal_broker_acknowledgement(tmp_path, error_code):
    from ib_insync import IB, Order as IBOrder, OrderStatus as IBOrderStatus, Stock, Trade

    api = _ib_api(tmp_path)
    api.ib = IB()
    api.ib.wrapper.clientId = 201
    trade = Trade(
        Stock("AAA", "SMART", "USD"),
        IBOrder(orderId=101, clientId=201),
        IBOrderStatus(orderId=101, clientId=201, status="PendingCancel"),
    )
    api.ib.wrapper.trades[(201, 101)] = trade
    api._trades["101"] = trade
    entry, _ = _bracket("buy", 101.0, 99.0)
    api._submitted_orders["101"] = entry
    api.ib.wrapper.error(101, error_code, "Cancellation rejected", "")
    assert trade.orderStatus.status == "Cancelled"
    assert api.order_status_snapshot("101") == "PendingCancel"
    assert api.list_open_orders()[0]["order"] is entry
    with pytest.raises(TimeoutError):
        await api.cancel_order("101", timeout_seconds=0.1)
    api.ib.wrapper.orderStatus(101, "Cancelled", 0, 1, 0, 0, 0, 0, 201, "")
    assert await api.cancel_order("101", timeout_seconds=0.1)


def _bracket(side, level, stop):
    entry = BrokerOrder(
        symbol="AAA", side=side, quantity=50, order_type="stop", stop_price=level,
        benchmark_type="stop_trigger", benchmark_price=level, order_role="entry",
        oca_group="ORB-AAA-1", gap_fill=False,
    )
    protective = BrokerOrder(
        symbol="AAA", side="sell" if side == "buy" else "buy", quantity=50,
        order_type="stop", stop_price=stop, benchmark_type="stop_trigger",
        benchmark_price=stop, order_role="protective_stop",
    )
    return entry, protective


class _FakeIBOrder:
    """Stand-in for ib_insync order classes (ib_insync is optional here)."""

    def __init__(self, order_type, action, quantity, aux_price=None, **kwargs):
        self.orderType = order_type
        self.action = action
        self.totalQuantity = quantity
        self.auxPrice = aux_price
        self.tif = kwargs.get("tif")
        self.orderId = 0
        self.parentId = 0
        self.transmit = True
        self.ocaGroup = ""
        self.ocaType = 0


@pytest.mark.asyncio
async def test_ib_brackets_share_one_oca_group_with_attached_stops(tmp_path, monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(
        ib_module, "StopOrder",
        lambda action, qty, stop, **kw: _FakeIBOrder("STP", action, qty, stop, **kw),
    )
    api = _ib_api(tmp_path)

    ids = await api.submit_oca_brackets(
        [_bracket("buy", 101.0, 99.0), _bracket("sell", 99.0, 101.0)],
        qualified_contract=object(),
    )

    parent_a, child_a, parent_b, child_b = api.ib.placed
    for parent, child in ((parent_a, child_a), (parent_b, child_b)):
        assert parent.orderType == "STP" and child.orderType == "STP"
        assert parent.ocaGroup == "ORB-AAA-1" and parent.ocaType == 1
        assert not getattr(child, "ocaGroup", "")
        assert parent.transmit is False and child.transmit is True
        assert child.parentId == parent.orderId
        assert child.action != parent.action
    assert (parent_a.action, parent_a.auxPrice, child_a.auxPrice) == ("BUY", 101.0, 99.0)
    assert (parent_b.action, parent_b.auxPrice, child_b.auxPrice) == ("SELL", 99.0, 101.0)
    assert ids == [
        (str(parent_a.orderId), str(child_a.orderId)),
        (str(parent_b.orderId), str(child_b.orderId)),
    ]
    stored = api.execution_store.get_submitted_order(str(child_a.orderId))
    assert stored["order_role"] == "protective_stop"
    assert stored["parent_order_id"] == str(parent_a.orderId)
    entries = api.execution_store.list_submitted_orders(symbol="AAA", order_role="entry")
    assert {row["oca_group"] for row in entries} == {"ORB-AAA-1"}


@pytest.mark.asyncio
async def test_ib_engine_refuses_a_second_pair_while_restored_orders_are_open():
    from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

    broker = SimpleNamespace(
        list_open_orders=lambda: [{"order": SimpleNamespace(symbol="AAA")}],
        submit_oca_brackets=None,
        add_execution_listener=lambda listener: None,
    )

    async def submit(*args, **kwargs):
        raise AssertionError("must not submit")

    broker.submit_oca_brackets = submit
    engine = InteractiveBrokersExecutionEngine(broker)
    engine._restored_open_symbols.add("AAA")

    with pytest.raises(RuntimeError, match="refusing duplicate"):
        await engine.submit_resting_entry_pair(
            "AAA", [RestingEntryLeg("buy", 10, 101.0, 99.0)], "ORB-AAA-2"
        )


# ---------------------------------------------------------------------------
# Orchestrator: placement, duplicate guard, exits, telemetry
# ---------------------------------------------------------------------------

def _orchestrator(tmp_path, hhmm=(9, 36)) -> TradingOrchestrator:
    config = AppSettings(
        environment="test",
        database_path=str(tmp_path / "trades.db"),
        broker={"broker_type": "mock", "mode": "paper"},
        trading={"symbols": ["AAA"]},
        dashboard={"enabled": False},
        operational_metrics={"enabled": False},
        notifications={"discord_webhook_url": None},
    )
    data = RuleSetLoader.from_name("orb_resting_stop_paper").model_dump()
    data["instruments"]["symbols"] = ["AAA"]
    data["portfolio"]["symbol_priority"] = ["AAA"]
    orchestrator = TradingOrchestrator(
        config=config,
        ruleset=StrategyRuleSet.model_validate(data),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, *hhmm), timezone="America/New_York"
        ),
        testing_mode=True,
    )
    exchange = _mock()
    orchestrator.exchange = exchange
    orchestrator.trade_executor = _executor(exchange)
    exchange.set_minute_bars("AAA", _or_minutes())
    positions = {}
    orchestrator.strategy = SimpleNamespace(
        config=SimpleNamespace(name="ORB"),
        _traded_today={},
        positions=positions,
        get_position=positions.get,
    )
    return orchestrator


ORB = {"orb_high": 101.0, "orb_low": 99.0, "orb_trading_date": SESSION}


def _or_minutes(day: date = SESSION, high: float = 101.0, low: float = 99.0, minutes: int = 5):
    """IBKR 1-minute TRADES bars for the opening range (09:30-09:34)."""
    import pandas as pd

    index = pd.date_range(
        pd.Timestamp(datetime(day.year, day.month, day.day, 9, 30), tz=ET),
        periods=minutes, freq="1min",
    )
    frame = pd.DataFrame(
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 5e4}, index=index
    )
    frame.iloc[min(1, minutes - 1), frame.columns.get_loc("high")] = high
    frame.iloc[min(3, minutes - 1), frame.columns.get_loc("low")] = low
    return frame


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["market_data:AAA", "account_risk", "ib_upstream"])
async def test_known_dependency_failure_blocks_resting_entry_until_recovery(tmp_path, failure):
    orchestrator = _orchestrator(tmp_path)
    await orchestrator.exchange.set_price("AAA", 100.0)
    await orchestrator._report_dependency_failure(failure, "Unavailable")
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    assert not _orders(orchestrator.exchange, "entry")
    await orchestrator._report_dependency_failure(failure, None)
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    assert len(_orders(orchestrator.exchange, "entry")) == 2
    await orchestrator.trade_executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_places_one_pair_per_session(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))

    assert len(_orders(orchestrator.exchange, "entry")) == 2
    entry = _orders(orchestrator.exchange, "entry")[0]
    assert entry.entry_signal_bar_timestamp.hour == 9 and entry.entry_signal_bar_timestamp.minute == 35
    await orchestrator.trade_executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_does_not_resubmit_after_a_restart(tmp_path):
    first = _orchestrator(tmp_path, (11, 0))
    await first.exchange.set_price("AAA", 100.0)
    first.exchange.has_submitted_entry = lambda symbol, since: True  # journal says placed

    await first._maybe_place_resting_entries("AAA", dict(ORB))

    assert _orders(first.exchange, "entry") == []
    assert first._entry_attempt_count("AAA", SESSION) == 1


@pytest.mark.asyncio
async def test_orchestrator_places_nothing_after_the_cutoff(tmp_path):
    orchestrator = _orchestrator(tmp_path, (15, 0))
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))

    assert _orders(orchestrator.exchange, "entry") == []


@pytest.mark.asyncio
async def test_broker_protected_position_gets_no_client_side_stop_exit(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (11, 0))
    exchange = orchestrator.exchange
    executor = orchestrator.trade_executor
    await exchange.set_price("AAA", 100.0)
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)

    from vibe.common.strategies.base import StrategyBase

    tracked = {"AAA": {"side": "buy", "entry_price": 101.0, "stop_loss": 99.0, "quantity": 50}}
    orchestrator.strategy = SimpleNamespace(
        config=SimpleNamespace(name="ORB"),
        _traded_today={},
        positions=tracked,
        get_position=lambda s: tracked.get(s),
        close_position=lambda s: tracked.pop(s, None),
        check_exit_conditions=lambda **kwargs: SimpleNamespace(
            exit_type="stop_loss", reason="client stop"
        ),
    )
    closes = []

    async def record_close(*args, **kwargs):
        closes.append(args)

    monkeypatch.setattr(orchestrator, "_close_position_with_notification", record_close)
    orchestrator._latest_bar_prices["AAA"] = 98.5  # beyond the stop, before the broker reacts

    await orchestrator._monitor_open_positions()
    assert closes == []  # the broker stop owns the stop-loss

    # The broker stop fills: the flat broker position is the source of truth.
    await exchange.set_price("AAA", 98.9)
    await exchange.process_pending_orders()
    await orchestrator._monitor_open_positions()
    assert closes == []
    assert "AAA" not in tracked
    assert not executor.has_protective_stop("AAA")
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_broker_protected_position_is_closed_at_eod(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (15, 55))
    executor = orchestrator.trade_executor
    executor._active_protective["AAA"] = "stop-1"
    await orchestrator.exchange.set_price("AAA", 100.0)
    await orchestrator.exchange.submit_order("AAA", "buy", 10, order_type="market", price=100.0)
    tracked = {"AAA": {"side": "buy", "entry_price": 100.0, "stop_loss": 99.0, "quantity": 10}}
    orchestrator.strategy = SimpleNamespace(
        config=SimpleNamespace(name="ORB"), positions=tracked,
        get_position=lambda s: tracked.get(s),
    )
    closes = []

    async def record_close(symbol, pos, price, reason):
        closes.append((symbol, reason))

    monkeypatch.setattr(orchestrator, "_close_position_with_notification", record_close)

    await orchestrator._monitor_open_positions()

    assert closes == [("AAA", "time_exit")]


def test_resting_fill_telemetry_records_role_gap_fill_and_ticks(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    recorded = []
    orchestrator.operational_metrics_store = SimpleNamespace(
        record_metric=lambda **kwargs: recorded.append(kwargs)
    )
    orchestrator.exchange = SimpleNamespace(
        order_lifecycle=lambda order_id: {
            "order_role": "entry", "gap_fill": True, "benchmark_type": "stop_trigger",
        }
    )
    event = OrderEvent(
        event_id="EXECUTION:e1", account_id="DU1", broker="interactive_brokers",
        broker_order_id="11", event_type="ORDER_FILLED", symbol="AAA", side="buy",
        quantity=50, occurred_at=datetime(2026, 7, 20, 9, 41, tzinfo=timezone.utc),
        price=101.04, execution_id="e1", benchmark_type="stop_trigger",
        benchmark_price=101.0, slippage_amount=0.04, slippage_bps=3.96,
        filled_at=datetime(2026, 7, 20, 9, 41, tzinfo=timezone.utc),
    )

    orchestrator._record_dashboard_fill_metrics(event=event)

    ticks = [m for m in recorded if m["metric_name"] == "slippage_ticks"]
    assert len(ticks) == 1 and ticks[0]["metric_value"] == pytest.approx(4.0)
    assert ticks[0]["dimensions"]["order_role"] == "entry"
    assert ticks[0]["dimensions"]["gap_fill"] == "true"


# ---------------------------------------------------------------------------
# Late E0: reconcile broker executions first
# ---------------------------------------------------------------------------

def _warmup(exchange, manager):
    return WarmupPhaseManager(
        SimpleNamespace(
            config=SimpleNamespace(notifications=SimpleNamespace(discord_webhook_url=None)),
            market_scheduler=MockMarketScheduler(
                initial_date=datetime.now(ET).replace(tzinfo=None, hour=10, minute=30),
                timezone="America/New_York",
            ),
            portfolio_risk_manager=manager,
            exchange=exchange,
            trade_executor=None,
        )
    )


class _ReconcilingExchange:
    """A flat IB-like account whose local journal is empty until reconciled."""

    def __init__(self, broker_executions=None, fail=False):
        self.journal = []
        self.broker_executions = broker_executions or []
        self.fail = fail

    async def list_positions(self):
        return []

    def list_open_orders(self):
        return []

    async def reconcile_todays_executions(self):
        if self.fail:
            raise TimeoutError("reqExecutions timed out")
        self.journal.extend(self.broker_executions)
        return len(self.broker_executions)

    def list_durable_executions(self):
        return list(self.journal)

    async def get_account(self):
        return SimpleNamespace(broker_equity=95_000.0, equity=95_000.0, account_id="DU1")


def _fresh_manager():
    return PortfolioRiskManager(PortfolioConfig(), PositionSizer(risk_pct=0.01))


@pytest.mark.asyncio
async def test_stop_fill_while_bot_was_down_blocks_a_late_snapshot():
    filled_today = datetime.now(ET).replace(hour=10, minute=5).astimezone(timezone.utc)
    exchange = _ReconcilingExchange(
        broker_executions=[{"execution_id": "x1", "filled_at": filled_today.isoformat()}]
    )
    manager = _fresh_manager()

    assert await _warmup(exchange, manager)._take_session_equity_snapshot() is False
    assert manager.session_equity is None
    assert "execution(s) today" in manager.halt_detail


@pytest.mark.asyncio
async def test_failed_execution_reconciliation_counts_as_touched():
    manager = _fresh_manager()

    assert await _warmup(_ReconcilingExchange(fail=True), manager)._take_session_equity_snapshot() is False
    assert "execution history unavailable" in manager.halt_detail


@pytest.mark.asyncio
async def test_reconciled_quiet_day_allows_a_late_snapshot():
    manager = _fresh_manager()

    assert await _warmup(_ReconcilingExchange(), manager)._take_session_equity_snapshot() is True
    assert manager.snapshot.late is True and manager.session_equity == 95_000.0


@pytest.mark.asyncio
async def test_ib_engine_reconciliation_is_strict():
    from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

    calls = []

    async def reconcile(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("IB execution history unavailable: not connected")

    broker = SimpleNamespace(
        reconcile_executions=reconcile,
        list_durable_executions=lambda: [],
        add_execution_listener=lambda listener: None,
    )
    engine = InteractiveBrokersExecutionEngine(broker)

    with pytest.raises(RuntimeError, match="not connected"):
        await engine.reconcile_todays_executions()
    assert calls == [{"strict": True}]


@pytest.mark.asyncio
async def test_fill_callbacks_drive_the_bracket_lifecycle_end_to_end(tmp_path):
    from vibe.common.strategies import ORBStrategy
    from vibe.common.strategies.orb import ORBStrategyConfig

    orchestrator = _orchestrator(tmp_path, (9, 36))
    exchange = orchestrator.exchange
    executor = TradeExecutor(
        exchange=exchange,
        order_manager=OrderManager(
            exchange=exchange,
            on_order_created=orchestrator._on_order_created,
            on_order_filled=orchestrator._on_order_filled,
            on_order_cancelled=orchestrator._on_order_cancelled,
        ),
        position_sizer=PositionSizer(risk_pct=0.01),
        risk_manager=_risk_manager(),
    )
    orchestrator.trade_executor = executor
    orchestrator.strategy = ORBStrategy(config=ORBStrategyConfig(name="ORB"))
    await exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()

    async def wait_for(condition):
        for _ in range(50):
            if condition():
                return True
            await asyncio.sleep(0.1)
        return False

    assert await wait_for(lambda: executor.has_protective_stop("AAA"))
    tracked = orchestrator.strategy.get_position("AAA")
    assert tracked["side"] == "buy" and tracked["stop_loss"] == 99.0
    sell_entry = next(o for o in _orders(exchange, "entry") if o.side == "sell")
    assert sell_entry.status == OrderStatus.CANCELLED
    assert await wait_for(lambda: not executor.has_pending_entry("AAA"))

    await exchange.set_price("AAA", 98.9)
    await exchange.process_pending_orders()

    assert await wait_for(lambda: orchestrator.strategy.get_position("AAA") is None)
    assert not executor.has_protective_stop("AAA")
    assert await exchange.get_position("AAA") is None
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_confirmed_stop_fill_never_sends_an_exit_even_with_a_stale_position(monkeypatch):
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    stale = await exchange.get_position("AAA")
    await exchange.set_price("AAA", 98.9)
    await exchange.process_pending_orders()  # the stop fills at the broker

    async def lagging_position(symbol):
        return stale  # broker portfolio not yet updated

    monkeypatch.setattr(exchange, "get_position", lagging_position)
    orders_before = len(exchange.get_all_orders())

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert not result.success and "protective stop" in result.reason
    assert len(exchange.get_all_orders()) == orders_before
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_partially_filled_stop_caps_the_close_at_its_remainder(monkeypatch):
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    stop = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    stop.filled_qty = 40.0  # the broker filled part of the stop
    full = await exchange.get_position("AAA")
    remainder = full.model_copy(update={"quantity": full.quantity - 40})
    reads = iter([full, remainder])  # the broker catches up on the second read

    async def lagging_position(symbol):
        return next(reads, remainder)

    monkeypatch.setattr(exchange, "get_position", lagging_position)
    executor.position_agreement_timeout_s = 2.0
    submitted = []
    original = executor.order_manager.submit_order

    async def record(**kwargs):
        submitted.append(kwargs["quantity"])
        return await original(**kwargs)

    monkeypatch.setattr(executor.order_manager, "submit_order", record)

    await executor._close_position("AAA", exit_reason="time_exit")

    assert submitted == [int(stop.quantity) - 40]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_partial_stop_close_aborts_and_alerts_when_the_broker_disagrees(monkeypatch):
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    alerts = []

    async def on_alert(title, message):
        alerts.append(title)

    executor.on_alert = on_alert
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    stop = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    stop.filled_qty = 40.0
    stale = await exchange.get_position("AAA")

    async def lagging_position(symbol):
        return stale

    monkeypatch.setattr(exchange, "get_position", lagging_position)
    executor.position_agreement_timeout_s = 0.0
    orders_before = len(exchange.get_all_orders())

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert not result.success and "disagrees" in result.reason
    # No market close; the cancelled stop is re-placed for the open remainder.
    (new_order,) = [o for o in exchange.get_all_orders().values()][orders_before:]
    assert (new_order.order_role, new_order.order_type, new_order.quantity) == (
        "protective_stop", "stop", stop.quantity - 40,
    )
    assert executor.protective_stop_order_id("AAA") == new_order.order_id
    assert alerts == ["Close aborted"]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_no_pair_is_placed_from_a_partial_opening_range(tmp_path):
    orchestrator = _orchestrator(tmp_path, (9, 33))
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))

    assert _orders(orchestrator.exchange, "entry") == []
    assert orchestrator._entry_attempt_count("AAA", SESSION) == 0


@pytest.mark.asyncio
async def test_r2_levels_come_from_ibkr_minute_bars_not_snapshots(tmp_path):
    orchestrator = _orchestrator(tmp_path, (9, 35, 12))
    # The aggregator's snapshot-built OR (sampled inside the true range) is ignored.
    orchestrator.exchange.set_minute_bars("AAA", _or_minutes(high=101.37, low=98.62))
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA", {"orb_high": 100.5, "orb_low": 99.5})

    entries = {o.side: o for o in _orders(orchestrator.exchange, "entry")}
    assert entries["buy"].stop_price == 101.37 and entries["sell"].stop_price == 98.62
    assert {(o.or_source, o.or_high, o.or_low) for o in entries.values()} == {
        ("mock_minute_bars", 101.37, 98.62)
    }
    await orchestrator.trade_executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r2_waits_for_every_or_minute_then_places(tmp_path):
    orchestrator = _orchestrator(tmp_path, (9, 35, 11))
    orchestrator.exchange.set_minute_bars("AAA", _or_minutes(minutes=4))  # 09:34 not served yet
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA")
    assert _orders(orchestrator.exchange, "entry") == []
    assert orchestrator._entry_attempt_count("AAA", SESSION) == 0

    orchestrator.exchange.set_minute_bars("AAA", _or_minutes())
    orchestrator.market_scheduler.set_time(9, 35, 16)
    await orchestrator._maybe_place_resting_entries("AAA")
    assert len(_orders(orchestrator.exchange, "entry")) == 2
    await orchestrator.trade_executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r2_missing_ibkr_bars_skip_the_symbol_with_one_alert(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (9, 35, 30))
    orchestrator.exchange.set_minute_bars("AAA", _or_minutes(minutes=3))
    alerts = []

    async def alert(title, message):
        alerts.append(title)

    monkeypatch.setattr(orchestrator, "_send_operator_alert", alert)
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA")
    assert alerts == [] and orchestrator._entry_attempt_count("AAA", SESSION) == 0

    orchestrator.market_scheduler.set_time(9, 36, 1)  # past or_end + 60 s
    await orchestrator._maybe_place_resting_entries("AAA")
    await orchestrator._maybe_place_resting_entries("AAA")

    assert _orders(orchestrator.exchange, "entry") == []
    assert alerts == ["Opening range unavailable"]
    assert orchestrator._entry_attempt_count("AAA", SESSION) == 1


@pytest.mark.asyncio
async def test_r2_mid_session_restart_places_from_ibkr_bars_at_once(tmp_path):
    orchestrator = _orchestrator(tmp_path, (10, 0))
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA")

    assert len(_orders(orchestrator.exchange, "entry")) == 2
    await orchestrator.trade_executor.order_manager.shutdown()


def test_resting_stop_or_must_be_whole_bars():
    data = RuleSetLoader.from_name("orb_resting_stop_paper").model_dump()
    data["strategy"]["orb_duration_minutes"] = 7
    with pytest.raises(ValueError, match="whole number"):
        StrategyRuleSet.model_validate(data)
    data["strategy"]["orb_duration_minutes"] = 15
    assert unsupported_settings(StrategyRuleSet.model_validate(data), "live") == []


# ---------------------------------------------------------------------------
# Milestone B review fixes
# ---------------------------------------------------------------------------

async def _filled_long(exchange, executor):
    await exchange.set_price("AAA", 100.0)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    executor.mark_entry_filled(buy.order_id)
    return buy


@pytest.mark.asyncio
async def test_b1_broker_cancelled_stop_is_replaced_for_the_open_quantity():
    exchange = _mock()
    executor = _executor(exchange)
    buy = await _filled_long(exchange, executor)
    lost = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    exchange._cancel_quietly(lost.order_id)  # IBKR rejected / cancelled it

    outcome = await executor.handle_protective_stop_ended(lost)

    assert outcome == "replaced"
    replacement = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    assert replacement.order_id != lost.order_id
    assert replacement.parent_order_id is None and replacement.order_role == "protective_stop"
    assert (replacement.side, replacement.quantity, replacement.stop_price) == (
        "sell", buy.quantity, lost.stop_price,
    )
    assert executor.order_manager.get_order(replacement.order_id).persistent
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_b1_failed_replacement_hands_over_to_the_client_stop_and_alerts(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    alerts = []

    async def on_alert(title, message):
        alerts.append(title)

    executor.on_alert = on_alert
    await _filled_long(exchange, executor)
    lost = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    exchange._cancel_quietly(lost.order_id)

    async def refuse(*args, **kwargs):
        raise RuntimeError("IBKR error 201")

    monkeypatch.setattr(exchange, "submit_protective_stop", refuse)

    assert await executor.handle_protective_stop_ended(lost) == "unprotected"
    assert not executor.has_protective_stop("AAA")
    assert alerts == ["Protective stop lost"]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_b1_our_own_cancel_is_not_mistaken_for_a_lost_stop():
    exchange = _mock()
    executor = _executor(exchange)
    await _filled_long(exchange, executor)

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert result.success
    assert not executor.has_protective_stop("AAA")
    standalone = [
        o for o in _orders(exchange, "protective_stop")
        if o.parent_order_id is None and o.status != OrderStatus.CANCELLED
    ]
    assert standalone == []
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_b1_monitor_checks_the_stop_status_every_cycle(tmp_path):
    orchestrator = _orchestrator(tmp_path, (11, 0))
    exchange = orchestrator.exchange
    executor = orchestrator.trade_executor
    buy = await _filled_long(exchange, executor)
    tracked = {"AAA": {"side": "buy", "entry_price": 101.0, "stop_loss": 99.0, "quantity": buy.quantity}}
    orchestrator.strategy = SimpleNamespace(
        config=SimpleNamespace(name="ORB"), _traded_today={}, positions=tracked,
        get_position=lambda s: tracked.get(s), close_position=lambda s: tracked.pop(s, None),
    )
    lost_id = executor.protective_stop_order_id("AAA")
    exchange._cancel_quietly(lost_id)

    await orchestrator._monitor_open_positions()

    assert executor.protective_stop_order_id("AAA") not in (None, lost_id)
    assert "AAA" in tracked  # the broker position is still open
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_b2_partial_parent_fill_stays_under_the_client_stop(tmp_path):
    orchestrator = _orchestrator(tmp_path, (9, 40))
    exchange = orchestrator.exchange
    executor = orchestrator.trade_executor
    await exchange.set_price("AAA", 100.0)
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    buy.filled_qty = 30.0  # IBKR filled part of the parent
    buy.status = OrderStatus.PARTIAL

    await orchestrator._on_order_filled(buy.order_id)

    assert not executor.has_protective_stop("AAA")  # the attached child is still inactive
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_b2_partial_entry_cancelled_at_cutoff_gets_a_standalone_stop(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (9, 40))
    exchange = orchestrator.exchange
    executor = orchestrator.trade_executor
    await exchange.set_price("AAA", 100.0)
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    buy.filled_qty = 30.0
    await exchange.cancel_order(buy.order_id)

    async def thirty_long(symbol):
        return SimpleNamespace(side="long", quantity=30.0)

    monkeypatch.setattr(exchange, "get_position", thirty_long)  # cutoff; IBKR also drops the child

    await orchestrator._on_order_cancelled(buy.order_id)

    stop = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    assert stop.parent_order_id is None
    assert (stop.side, stop.quantity, stop.stop_price) == ("sell", 30.0, 99.0)
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_b2_unprotectable_partial_entry_is_flattened_and_alerted(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (9, 40))
    exchange = orchestrator.exchange
    executor = orchestrator.trade_executor
    alerts, flattened = [], []

    async def on_alert(title, message):
        alerts.append(title)

    async def refuse(*args, **kwargs):
        raise RuntimeError("IBKR error 110")

    async def record_flatten(symbol, reason):
        flattened.append((symbol, reason))

    executor.on_alert = on_alert
    monkeypatch.setattr(exchange, "submit_protective_stop", refuse)
    monkeypatch.setattr(orchestrator, "_flatten_symbol", record_flatten)
    await exchange.set_price("AAA", 100.0)
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    buy.filled_qty = 30.0
    await exchange.cancel_order(buy.order_id)

    async def thirty_long(symbol):
        return SimpleNamespace(side="long", quantity=30.0)

    monkeypatch.setattr(exchange, "get_position", thirty_long)

    await orchestrator._on_order_cancelled(buy.order_id)

    assert flattened == [("AAA", "unprotected_partial_entry")]
    assert alerts == ["Partial entry unprotected"]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_s3_unconfirmed_deadline_cancel_keeps_monitoring_and_retries(monkeypatch):
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    alerts = []

    async def on_alert(title, message):
        alerts.append(title)

    manager = OrderManager(exchange=exchange, on_alert=on_alert)
    manager.CANCEL_ALERT_AFTER = 2
    manager.CANCEL_RETRY_BASE_S = 0.1
    executor = TradeExecutor(
        exchange=exchange, order_manager=manager,
        position_sizer=PositionSizer(risk_pct=0.01), risk_manager=_risk_manager(),
    )
    real_cancel = exchange.cancel_order
    failures = {}

    async def flaky_cancel(order_id):
        if failures.get(order_id, 0) < 2:
            failures[order_id] = failures.get(order_id, 0) + 1
            raise TimeoutError("Timed out waiting for terminal cancellation status")
        return await real_cancel(order_id)

    monkeypatch.setattr(exchange, "cancel_order", flaky_cancel)
    await _place(executor, cancel_after=0.05)
    entry_ids = [o.order_id for o in _orders(exchange, "entry")]

    for _ in range(80):
        await asyncio.sleep(0.1)
        if all(manager.get_order(i).terminal_status for i in entry_ids):
            break

    assert all(manager.get_order(i).terminal_status == OrderStatus.CANCELLED for i in entry_ids)
    assert "Order cancel unconfirmed" in alerts
    await manager.shutdown()


@pytest.mark.asyncio
async def test_s4_placed_legs_are_registered_even_if_a_later_leg_fails(tmp_path, monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(
        ib_module, "StopOrder",
        lambda action, qty, stop, **kw: _FakeIBOrder("STP", action, qty, stop, **kw),
    )
    api = _ib_api(tmp_path)
    calls = {"n": 0}
    real_place = api.ib.placeOrder

    def flaky_place(contract, order):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionError("socket closed")
        return real_place(contract, order)

    api.ib.placeOrder = flaky_place

    with pytest.raises(PartialPlacementError) as raised:
        await api.submit_oca_brackets(
            [_bracket("buy", 101.0, 99.0), _bracket("sell", 99.0, 101.0)],
            qualified_contract=object(),
        )

    assert len(api._trades) == 2 and len(api._submitted_orders) == 2
    assert len(raised.value.placed) == 1  # the complete, live buy bracket


@pytest.mark.asyncio
async def test_nit_parent_left_without_its_child_is_cancelled(tmp_path, monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(
        ib_module, "StopOrder",
        lambda action, qty, stop, **kw: _FakeIBOrder("STP", action, qty, stop, **kw),
    )
    api = _ib_api(tmp_path)
    calls = {"n": 0}
    real_place = api.ib.placeOrder

    def flaky_place(contract, order):
        calls["n"] += 1
        if calls["n"] == 2:  # the first child
            raise ConnectionError("socket closed")
        return real_place(contract, order)

    api.ib.placeOrder = flaky_place

    with pytest.raises(PartialPlacementError) as raised:
        await api.submit_oca_brackets([_bracket("buy", 101.0, 99.0)], qualified_contract=object())

    assert raised.value.placed == []
    assert api.ib.cancelled == [api.ib.placed[0].orderId]


@pytest.mark.asyncio
async def test_nit_partial_placement_monitors_live_legs_and_alerts(monkeypatch):
    from vibe.common.execution.base import PartialPlacementError as Partial

    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)
    alerts = []

    async def on_alert(title, message):
        alerts.append(title)

    executor.on_alert = on_alert
    real_submit = exchange.submit_resting_entry_pair

    async def partial(symbol, legs, group, lifecycle_metadata=None):
        placed = await real_submit(symbol, legs[:1], group, lifecycle_metadata)
        raise Partial("socket closed", placed)

    monkeypatch.setattr(exchange, "submit_resting_entry_pair", partial)

    result = await _place(executor)

    assert result.broker_contacted and result.order_id is not None
    assert executor.has_pending_entry("AAA")
    entry = _orders(exchange, "entry")[0]
    assert executor.order_manager.get_order(entry.order_id) is not None
    assert alerts == ["Resting entry placement incomplete"]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_s4_journal_failure_does_not_hide_live_orders(tmp_path, monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(
        ib_module, "StopOrder",
        lambda action, qty, stop, **kw: _FakeIBOrder("STP", action, qty, stop, **kw),
    )
    api = _ib_api(tmp_path)

    def broken_journal(payload):
        raise OSError("disk full")

    monkeypatch.setattr(api.execution_store, "upsert_submitted_order", broken_journal)

    ids = await api.submit_oca_brackets([_bracket("buy", 101.0, 99.0)], qualified_contract=object())

    assert len(ids) == 1 and len(api._trades) == 2


@pytest.mark.asyncio
async def test_s4_failed_submission_still_counts_as_todays_attempt(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (9, 36))
    await orchestrator.exchange.set_price("AAA", 100.0)

    async def broken_submit(*args, **kwargs):
        raise ConnectionError("lost after placeOrder")

    monkeypatch.setattr(orchestrator.exchange, "submit_resting_entry_pair", broken_submit)

    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))

    assert orchestrator._entry_attempt_count("AAA", SESSION) == 1


@pytest.mark.asyncio
async def test_s6_levels_are_rounded_to_the_tick_conservatively():
    exchange = _mock()
    await exchange.set_price("AAA", 100.0)
    executor = _executor(exchange)

    await _place(
        executor, long_level=101.004, short_level=98.996, long_stop=98.996, short_stop=101.004
    )

    entries = {o.side: o for o in _orders(exchange, "entry")}
    stops = {s.parent_order_id: s for s in _orders(exchange, "protective_stop")}
    assert entries["buy"].stop_price == 101.01  # buy trigger rounded up
    assert entries["sell"].stop_price == 98.99  # sell trigger rounded down
    assert stops[entries["buy"].order_id].stop_price == 99.0  # long's stop rounded up (tighter)
    assert stops[entries["sell"].order_id].stop_price == 101.0  # short's stop rounded down


@pytest.mark.asyncio
async def test_s7_entry_orders_record_placement_latency(tmp_path):
    orchestrator = _orchestrator(tmp_path, (9, 35))
    orchestrator.market_scheduler.set_time(9, 35, 12)  # the submission clock
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))

    latencies = [o.placement_latency_s for o in _orders(orchestrator.exchange, "entry")]
    assert latencies == [pytest.approx(12.0)] * 2
    await orchestrator.trade_executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_s8_non_linked_orders_keep_the_cancel_and_resubmit_path():
    from vibe.common.execution.base import OrderResponse
    from vibe.trading_bot.execution.order_manager import OrderRetryPolicy

    class Exchange:
        def __init__(self):
            self.orders = {
                "m1": Order(
                    order_id="m1", symbol="AAA", side="sell", quantity=10, price=100.0,
                    order_type="market", status=OrderStatus.CANCELLED, filled_qty=4.0,
                    avg_price=100.0,
                )
            }
            self.cancels, self.submitted = [], []

        async def get_order(self, order_id):
            return self.orders.get(order_id)

        async def cancel_order(self, order_id):
            self.cancels.append(order_id)
            return OrderResponse(order_id, OrderStatus.CANCELLED, 4.0, 100.0, 6.0)

        async def submit_order(self, **kwargs):
            self.submitted.append(kwargs["quantity"])
            order = Order(
                order_id="m2", symbol="AAA", side="sell", quantity=kwargs["quantity"],
                price=100.0, order_type="market", status=OrderStatus.FILLED,
                filled_qty=kwargs["quantity"], avg_price=100.0,
            )
            self.orders["m2"] = order
            return OrderResponse("m2", OrderStatus.FILLED, order.quantity, 100.0, 0.0)

    exchange = Exchange()
    cancelled = []

    async def on_cancelled(order_id):
        cancelled.append(order_id)

    manager = OrderManager(
        exchange=exchange,
        retry_policy=OrderRetryPolicy(base_delay_seconds=0.0, cancel_after_seconds=600),
        on_order_cancelled=on_cancelled,
    )
    from vibe.trading_bot.execution.order_manager import ManagedOrder

    manager._orders["m1"] = ManagedOrder(order_id="m1", order=exchange.orders["m1"])
    await manager._monitor_order("m1")

    # The broker-cancel shortcut is for linked orders only: the remainder is resubmitted.
    assert exchange.submitted == [6.0]
    assert cancelled == []
    await manager.shutdown()


@pytest.mark.asyncio
async def test_s8_ib_status_refresh_only_touches_linked_orders():
    from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

    broker = SimpleNamespace(
        order_status_snapshot=lambda order_id: "Cancelled",
        add_execution_listener=lambda listener: None,
    )
    engine = InteractiveBrokersExecutionEngine(broker)
    plain = Order(order_id="1", symbol="AAA", side="sell", quantity=10, price=100.0,
                  order_type="market", status=OrderStatus.SUBMITTED)
    linked = _restored("2", "sell", "protective_stop", 99.0, parent="9")
    engine._orders = {"1": plain, "2": linked}

    assert (await engine.get_order("1")).status == OrderStatus.SUBMITTED
    assert (await engine.get_order("2")).status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_s2_entry_fill_is_tracked_even_when_the_broker_position_lags(tmp_path, monkeypatch):
    from vibe.common.strategies import ORBStrategy
    from vibe.common.strategies.orb import ORBStrategyConfig

    orchestrator = _orchestrator(tmp_path, (9, 40))
    exchange = orchestrator.exchange
    orchestrator.strategy = ORBStrategy(config=ORBStrategyConfig(name="ORB"))
    await exchange.set_price("AAA", 100.0)
    await orchestrator._maybe_place_resting_entries("AAA", dict(ORB))
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")

    async def lagging(symbol):
        return None  # ib.portfolio() has not caught up with the execution

    monkeypatch.setattr(exchange, "get_position", lagging)

    await orchestrator._sync_open_trade_after_entry_fill(buy)

    tracked = orchestrator.strategy.get_position("AAA")
    assert tracked is not None
    assert (tracked["side"], tracked["quantity"], tracked["entry_price"]) == (
        "buy", buy.filled_qty, buy.avg_price,
    )
    assert orchestrator.trade_executor.get_pending_entry(buy.order_id) is None
    await orchestrator.trade_executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_s2_untracked_broker_position_is_flattened_at_eod(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (15, 56))
    executor = orchestrator.trade_executor
    flattened = []

    async def positions():
        return {"AAA": 50.0, "BBB": 10.0}

    async def record_flatten(symbol, reason):
        flattened.append((symbol, reason))

    monkeypatch.setattr(executor, "account_positions", positions)
    monkeypatch.setattr(orchestrator, "_flatten_symbol", record_flatten)
    orchestrator.strategy.positions["BBB"] = {"side": "buy"}  # tracked: normal EOD path

    await orchestrator._monitor_untracked_positions(
        orchestrator.market_scheduler.now()
    )

    assert flattened == [("AAA", "time_exit")]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_s2_untracked_positions_are_left_alone_before_eod(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (14, 0))
    executor = orchestrator.trade_executor
    flattened = []

    async def positions():
        return {"AAA": 50.0}

    async def record_flatten(symbol, reason):
        flattened.append(symbol)

    monkeypatch.setattr(executor, "account_positions", positions)
    monkeypatch.setattr(orchestrator, "_flatten_symbol", record_flatten)

    await orchestrator._monitor_untracked_positions(orchestrator.market_scheduler.now())

    assert flattened == []
    await executor.order_manager.shutdown()



# ---------------------------------------------------------------------------
# Milestone B review, round 2
# ---------------------------------------------------------------------------

def _standalone_stop(**overrides):
    fields = dict(
        symbol="AAA", side="sell", quantity=50, order_type="stop", stop_price=99.0,
        benchmark_type="stop_trigger", benchmark_price=99.0, strategy_stop_price=99.0,
        exit_reason="protective_stop", order_role="protective_stop",
    )
    fields.update(overrides)
    return BrokerOrder(**fields)


async def _restart_engine(tmp_path, ib_order):
    """A fresh IB adapter + engine that finds ``ib_order`` working at the broker."""
    from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

    api = _ib_api(tmp_path)
    api.ib.openTrades = lambda: [
        SimpleNamespace(
            order=ib_order,
            orderStatus=SimpleNamespace(status="PreSubmitted", filled=0.0, avgFillPrice=0.0),
            contract=SimpleNamespace(symbol="AAA"),
        )
    ]
    api._reconcile_open_orders()

    async def connect():
        return True

    broker = SimpleNamespace(
        connect=connect,
        list_durable_executions=lambda: [],
        list_open_orders=api.list_open_orders,
        add_execution_listener=lambda listener: None,
    )
    engine = InteractiveBrokersExecutionEngine(broker)
    await engine.initialize()
    return engine


@pytest.mark.asyncio
async def test_r1_standalone_stop_keeps_its_role_across_a_restart(tmp_path, monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(
        ib_module, "StopOrder",
        lambda action, qty, stop, **kw: _FakeIBOrder("STP", action, qty, stop, **kw),
    )
    api = _ib_api(tmp_path)
    stop_id = await api.submit_standalone_stop(_standalone_stop(), qualified_contract=object())

    journalled = api.execution_store.get_submitted_order(stop_id)
    assert journalled["order_role"] == "protective_stop"

    engine = await _restart_engine(tmp_path, api._trades[stop_id].order)
    (restored,) = engine.list_open_orders()
    assert restored.order_role == "protective_stop"

    executor = _executor(_mock())
    executor.order_manager.restore_open_orders([restored])
    executor.restore_pending_entries([restored])
    assert executor.has_protective_stop("AAA")
    assert not executor.has_pending_entry("AAA")
    managed = executor.order_manager.get_order(stop_id)
    assert managed.persistent and managed.linked
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r1_old_journal_rows_are_classified_by_exit_reason(tmp_path, monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(
        ib_module, "StopOrder",
        lambda action, qty, stop, **kw: _FakeIBOrder("STP", action, qty, stop, **kw),
    )
    api = _ib_api(tmp_path)
    # A row journalled before submit_order carried the role.
    stop_id = await api.submit_standalone_stop(
        _standalone_stop(order_role=None), qualified_contract=object()
    )
    assert api.execution_store.get_submitted_order(stop_id)["order_role"] is None

    engine = await _restart_engine(tmp_path, api._trades[stop_id].order)

    (restored,) = engine.list_open_orders()
    assert restored.order_role == "protective_stop"


@pytest.mark.asyncio
async def test_r1_roleless_stop_closing_an_open_position_is_adopted(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (11, 0))
    stop = Order(
        order_id="77", symbol="AAA", side="sell", quantity=50, price=99.0,
        order_type="stop", status=OrderStatus.SUBMITTED, stop_price=99.0,
    )
    entry_like = Order(
        order_id="78", symbol="BBB", side="buy", quantity=10, price=50.0,
        order_type="stop", status=OrderStatus.SUBMITTED, stop_price=50.0,
    )

    async def position(symbol):
        return SimpleNamespace(side="long", quantity=50.0) if symbol == "AAA" else None

    monkeypatch.setattr(orchestrator.exchange, "get_position", position)

    await orchestrator._adopt_restored_protective_stops([stop, entry_like])

    assert stop.order_role == "protective_stop"
    assert entry_like.order_role is None
    executor = orchestrator.trade_executor
    executor.order_manager.restore_open_orders([stop])
    assert executor.order_manager.get_order("77").persistent
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r3_replacement_is_not_placed_behind_a_pending_close():
    exchange = _mock()
    executor = _executor(exchange)
    await _filled_long(exchange, executor)
    lost = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    exchange._cancel_quietly(lost.order_id)
    executor._pending_closes["AAA"] = "close-1"  # EOD close in flight
    stops_before = len(_orders(exchange, "protective_stop"))

    outcome = await executor.handle_protective_stop_ended(lost)

    assert outcome == "closing"
    assert len(_orders(exchange, "protective_stop")) == stops_before
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r3_close_waits_for_an_in_flight_replacement_and_cancels_it(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    await _filled_long(exchange, executor)
    lost = await exchange.get_order(executor.protective_stop_order_id("AAA"))
    exchange._cancel_quietly(lost.order_id)
    gate = asyncio.Event()
    real_get_position = exchange.get_position
    calls = {"n": 0}

    async def slow_position(symbol):
        calls["n"] += 1
        if calls["n"] == 1:
            await gate.wait()  # the handler is mid-flight, holding the lock
        return await real_get_position(symbol)

    monkeypatch.setattr(exchange, "get_position", slow_position)
    handler = asyncio.create_task(executor.handle_protective_stop_ended(lost))
    await asyncio.sleep(0.05)
    close = asyncio.create_task(executor._close_position("AAA", exit_reason="time_exit"))
    await asyncio.sleep(0.05)
    assert not close.done()  # serialised behind the re-protection
    gate.set()

    assert await handler == "replaced"
    result = await close
    assert result.success
    working_stops = [
        o for o in _orders(exchange, "protective_stop")
        if o.status not in (OrderStatus.CANCELLED, OrderStatus.FILLED)
    ]
    assert working_stops == []
    assert await exchange.get_position("AAA") is None
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r3_orphan_stop_on_a_flat_account_is_cancelled():
    exchange = _mock()
    executor = _executor(exchange)
    alerts = []

    async def on_alert(title, message):
        alerts.append(title)

    executor.on_alert = on_alert
    stop = await exchange.submit_protective_stop("AAA", "sell", 50, 99.0, "ORB")
    executor._activate_protective("AAA", stop.order_id)
    executor.order_manager.track_orders([stop], persistent=True)

    assert await executor.sweep_orphan_protective_stops() == []  # grace period

    executor.orphan_stop_grace_s = 0.0
    assert await executor.sweep_orphan_protective_stops() == [stop.order_id]
    assert (await exchange.get_order(stop.order_id)).status == OrderStatus.CANCELLED
    assert not executor.has_protective_stop("AAA")
    assert alerts == ["Orphan protective stop cancelled"]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r3_stop_is_kept_while_fills_show_an_open_position(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    executor.orphan_stop_grace_s = 0.0
    stop = await exchange.submit_protective_stop("AAA", "sell", 50, 99.0, "ORB")
    executor._activate_protective("AAA", stop.order_id)

    async def fills_say_long(symbol):
        return 50.0  # the broker position lags the entry fill

    monkeypatch.setattr(exchange, "session_net_quantity", fills_say_long, raising=False)

    assert await executor.sweep_orphan_protective_stops() == []
    assert executor.has_protective_stop("AAA")
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r4_parent_completed_during_a_close_has_its_child_cancelled():
    exchange = _mock()
    executor = _executor(exchange)
    await exchange.set_price("AAA", 100.0)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()  # filled; the fill callback has not run yet
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    child = next(o for o in _orders(exchange, "protective_stop") if o.parent_order_id == buy.order_id)
    assert not executor.has_protective_stop("AAA")

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert result.success
    assert (await exchange.get_order(child.order_id)).status == OrderStatus.CANCELLED
    assert await exchange.get_position("AAA") is None
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r5_close_is_skipped_when_todays_fills_are_flat(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    await _filled_long(exchange, executor)
    executor.clear_protective_stop("AAA")  # the stop-fill callback already ran

    async def flat_by_fills(symbol):
        return 0.0  # the stop filled; ib.portfolio() still shows the position

    monkeypatch.setattr(exchange, "session_net_quantity", flat_by_fills, raising=False)
    orders_before = len(exchange.get_all_orders())
    alerts = []

    async def on_alert(title, message):
        alerts.append(title)

    executor.on_alert = on_alert

    result = await executor._close_position("AAA", exit_reason="daily_loss_limit")

    assert not result.success and "flat by today's fills" in result.reason
    assert len(exchange.get_all_orders()) == orders_before
    assert alerts == ["Close aborted"]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r5_close_is_sized_by_fills_when_the_broker_shows_more(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    await _filled_long(exchange, executor)
    executor.clear_protective_stop("AAA")

    async def partly_closed(symbol):
        return 30.0

    monkeypatch.setattr(exchange, "session_net_quantity", partly_closed, raising=False)
    submitted = []
    original = executor.order_manager.submit_order

    async def record(**kwargs):
        submitted.append(kwargs["quantity"])
        return await original(**kwargs)

    monkeypatch.setattr(executor.order_manager, "submit_order", record)

    await executor._close_position("AAA", exit_reason="time_exit")

    assert submitted == [30]
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_rv1_completing_a_partially_filled_parent_activates_its_stop(tmp_path):
    from vibe.common.strategies import ORBStrategy
    from vibe.common.strategies.orb import ORBStrategyConfig

    orchestrator = _orchestrator(tmp_path, (9, 40))
    orchestrator.strategy = ORBStrategy(config=ORBStrategyConfig(name="ORB"))
    exchange = orchestrator.exchange
    executor = orchestrator.trade_executor
    await exchange.set_price("AAA", 100.0)
    await orchestrator._maybe_place_resting_entries("AAA")
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    buy.filled_qty, buy.avg_price, buy.status = 30.0, 101.02, OrderStatus.PARTIAL

    await orchestrator._on_order_filled(buy.order_id)

    assert executor.get_pending_entry(buy.order_id) is not None  # still working
    assert not executor.has_protective_stop("AAA")

    buy.filled_qty, buy.status = float(buy.quantity), OrderStatus.FILLED
    await orchestrator._on_order_filled(buy.order_id)

    assert executor.has_protective_stop("AAA")
    assert executor.get_pending_entry(buy.order_id) is None
    await executor.order_manager.shutdown()


def test_r8_ib_engine_counts_a_corrected_execution_once():
    from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

    rows = []
    broker = SimpleNamespace(
        add_execution_listener=lambda listener: None,
        list_durable_executions=lambda: list(rows),
    )
    engine = InteractiveBrokersExecutionEngine(broker)
    seen = []
    engine.add_execution_listener(seen.append)

    def execution(exec_id, price):
        return {
            "execution_id": exec_id, "broker_order_id": "5", "symbol": "AAA", "side": "buy",
            "quantity": 50.0, "price": price, "filled_at": "2026-07-20T13:40:00+00:00",
            "order_metadata": {"order_role": "entry", "quantity": 50},
        }

    original, correction = execution("00a.11b.01.01", 101.05), execution("00a.11b.01.02", 101.03)
    rows.extend([original, correction])
    engine._on_broker_execution(original)
    engine._on_broker_execution(correction)
    engine._on_broker_execution(original)  # a late replay of the old revision

    order = engine._orders["5"]
    assert order.filled_qty == 50.0 and order.avg_price == pytest.approx(101.03)
    assert order.execution_ids == ["00a.11b.01.01"]
    assert [e["execution_id"] for e in seen] == ["00a.11b.01.01", "00a.11b.01.01"]
    (durable,) = engine.list_durable_executions()
    assert durable["execution_id"] == "00a.11b.01.01" and durable["price"] == 101.03
    assert durable["raw_execution_id"] == "00a.11b.01.02"


@pytest.mark.asyncio
async def test_r8_session_net_quantity_uses_deduped_fills_from_the_resting_entry():
    from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine

    today = datetime.now(ET).replace(hour=10, minute=0, second=0, microsecond=0)

    def row(exec_id, side, qty, minute, metadata):
        return {
            "execution_id": exec_id, "broker_order_id": exec_id[:3], "symbol": "AAA",
            "side": side, "quantity": qty, "price": 100.0,
            "filled_at": (today + timedelta(minutes=minute)).isoformat(),
            "order_metadata": metadata,
        }

    rows = [
        row("c01.x.01.01", "sell", 20, -20, {"order_role": "flatten"}),  # carryover flatten
        row("e01.x.01.01", "buy", 50, 0, {"order_role": "entry", "oca_group": "ORB-AAA-1"}),
        row("e01.x.01.02", "buy", 50, 0, {"order_role": "entry", "oca_group": "ORB-AAA-1"}),
        row("s01.x.01.01", "sell", 20, 5, {"order_role": "protective_stop"}),
    ]
    engine = InteractiveBrokersExecutionEngine(
        SimpleNamespace(add_execution_listener=lambda l: None, list_durable_executions=lambda: rows)
    )

    assert await engine.session_net_quantity("AAA") == 30.0
    assert await engine.session_net_quantity("BBB") is None


def test_nit_untracked_broker_positions_keep_the_active_cadence(tmp_path):
    orchestrator = _orchestrator(tmp_path, (15, 56))
    orchestrator.exchange = SimpleNamespace()  # IBKR-like: no synchronous position list
    orchestrator.trade_executor = SimpleNamespace(
        _active_protective={}, _pending_entries={}, _pending_closes={}
    )

    assert orchestrator._has_active_positions() is False
    orchestrator._broker_position_symbols = {"AAA"}
    assert orchestrator._has_active_positions() is True



# ---------------------------------------------------------------------------
# Milestone B review, round 3
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r3_1_fill_disagreement_aborts_before_the_stop_is_cancelled(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    await _filled_long(exchange, executor)
    stop_id = executor.protective_stop_order_id("AAA")

    async def fills_say_short(symbol):
        return -50.0

    monkeypatch.setattr(exchange, "session_net_quantity", fills_say_short, raising=False)
    orders_before = len(exchange.get_all_orders())

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert not result.success and "disagrees" in result.reason
    assert executor.protective_stop_order_id("AAA") == stop_id  # still protected
    assert (await exchange.get_order(stop_id)).status != OrderStatus.CANCELLED
    assert len(exchange.get_all_orders()) == orders_before
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r3_1_close_order_failure_after_the_stop_cancel_re_protects(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    buy = await _filled_long(exchange, executor)
    old_stop = executor.protective_stop_order_id("AAA")

    async def broken_submit(**kwargs):
        raise ConnectionError("socket closed")

    monkeypatch.setattr(executor.order_manager, "submit_order", broken_submit)

    result = await executor._close_position("AAA", exit_reason="time_exit")

    assert not result.success
    new_stop_id = executor.protective_stop_order_id("AAA")
    assert new_stop_id not in (None, old_stop)
    new_stop = await exchange.get_order(new_stop_id)
    assert (new_stop.side, new_stop.quantity, new_stop.stop_price) == ("sell", buy.quantity, 99.0)
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_r3_3_entry_cancel_queued_behind_a_close_places_no_stop(monkeypatch):
    exchange = _mock()
    executor = _executor(exchange)
    await exchange.set_price("AAA", 100.0)
    await _place(executor)
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")
    buy.filled_qty = 30.0
    ended = buy.model_copy(update={"status": OrderStatus.CANCELLED})
    lock = executor._symbol_lock("AAA")
    await lock.acquire()  # a close is running
    handler = asyncio.create_task(executor.handle_entry_cancelled(ended))
    await asyncio.sleep(0.05)
    lock.release()  # the close finished: the account is flat

    assert await handler == "flat"
    assert not executor.has_protective_stop("AAA")
    standalone = [o for o in _orders(exchange, "protective_stop") if o.parent_order_id is None]
    assert standalone == []
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_round3_late_fill_callback_does_not_revive_a_cancelled_child():
    exchange = _mock()
    executor = _executor(exchange)
    await exchange.set_price("AAA", 100.0)
    await _place(executor)
    await exchange.set_price("AAA", 101.2)
    await exchange.process_pending_orders()
    buy = next(o for o in _orders(exchange, "entry") if o.side == "buy")

    assert (await executor._close_position("AAA", exit_reason="time_exit")).success
    executor.mark_entry_filled(buy.order_id, "AAA")  # the fill callback, arriving late

    assert not executor.has_protective_stop("AAA")
    await executor.order_manager.shutdown()


@pytest.mark.asyncio
async def test_round3_blocked_entries_do_not_refetch_ibkr_history(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (10, 0))
    await orchestrator.exchange.set_price("AAA", 100.0)
    fetches = []
    real_fetch = orchestrator.exchange.fetch_opening_range_bars

    async def counting(*args):
        fetches.append(args[0])
        return await real_fetch(*args)

    async def blocked(symbol, **kwargs):
        from vibe.trading_bot.execution.trade_executor import ExecutionResult

        return ExecutionResult(success=False, reason="Entries blocked: daily loss limit")

    monkeypatch.setattr(orchestrator.exchange, "fetch_opening_range_bars", counting)
    monkeypatch.setattr(orchestrator.trade_executor, "place_resting_stop_entries", blocked)

    for _ in range(3):
        await orchestrator._maybe_place_resting_entries("AAA")

    assert fetches == ["AAA"]
    assert orchestrator._entry_attempt_count("AAA", SESSION) == 0


@pytest.mark.asyncio
async def test_r3_4_first_or_request_waits_ten_seconds_after_or_end(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path, (9, 35, 5))
    fetches = []
    real_fetch = orchestrator.exchange.fetch_opening_range_bars

    async def counting(*args):
        fetches.append(args[0])
        return await real_fetch(*args)

    monkeypatch.setattr(orchestrator.exchange, "fetch_opening_range_bars", counting)
    await orchestrator.exchange.set_price("AAA", 100.0)

    await orchestrator._maybe_place_resting_entries("AAA")
    assert fetches == []

    orchestrator.market_scheduler.set_time(9, 35, 10)
    await orchestrator._maybe_place_resting_entries("AAA")
    assert fetches == ["AAA"] and len(_orders(orchestrator.exchange, "entry")) == 2
    await orchestrator.trade_executor.order_manager.shutdown()


def test_r3_2_gap_fill_entries_log_fill_minus_submission(tmp_path, caplog):
    import logging

    orchestrator = _orchestrator(tmp_path, (9, 40))
    submitted = datetime(2026, 7, 20, 13, 35, 12, tzinfo=timezone.utc)
    order = SimpleNamespace(
        symbol="AAA", side="buy", order_id="9", gap_fill=True,
        submitted_at=submitted, filled_at=submitted + timedelta(seconds=2.25),
    )
    caplog.set_level(logging.INFO)

    orchestrator._log_gap_fill_timing(order)
    orchestrator._log_gap_fill_timing(SimpleNamespace(**{**order.__dict__, "gap_fill": False}))

    lines = [r.getMessage() for r in caplog.records if "[GAP FILL]" in r.getMessage()]
    assert lines == ["[GAP FILL] AAA buy entry 9 filled 2.250 s after submission"]
