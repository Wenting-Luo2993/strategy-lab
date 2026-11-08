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
        self.placed.append(order)
        return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status="PreSubmitted"))


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
    orchestrator.strategy = SimpleNamespace(
        config=SimpleNamespace(name="ORB"),
        _traded_today={},
        positions={},
    )
    return orchestrator


ORB = {"orb_high": 101.0, "orb_low": 99.0, "orb_trading_date": SESSION}


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
