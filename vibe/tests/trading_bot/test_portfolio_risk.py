"""Account-level risk management (``portfolio:`` rulesets), stage 1.

Covers the cross-symbol position cap (regression for SimpleRiskManager only
seeing the current symbol), the equity-headroom rule with long + short +
pending exposure, the daily loss limit and its reset at the next session, and
blocking entries when the warm-up equity snapshot fails.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

import pytest

from vibe.common.models import Position
from vibe.common.risk import PositionSizer
from vibe.common.risk.portfolio_sizing import (
    OpenExposure,
    PendingExposure,
    equity_headroom,
    reserved_pending_notional,
)
from vibe.common.ruleset.models import PortfolioConfig
from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.trading_bot.core.phases.warmup import WarmupPhaseManager
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import OrderManager
from vibe.trading_bot.execution.portfolio_risk import (
    DAILY_LOSS_LIMIT,
    NET_LIQUIDATION_UNAVAILABLE,
    RISK_EVALUATION_FAILED,
    SNAPSHOT_FAILED,
    SNAPSHOT_MISSING,
    PortfolioRiskManager,
    SessionEquitySnapshot,
)
from vibe.trading_bot.execution.trade_executor import (
    EntryOrderIntent,
    SimpleRiskManager,
    TradeExecutor,
)
from vibe.trading_bot.notifications.formatter import DiscordNotificationFormatter
from vibe.trading_bot.notifications.payloads import RiskAlertPayload

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 7, 20)


def _config(**overrides) -> PortfolioConfig:
    values = dict(
        sizing_equity_basis="session_start",
        max_symbol_exposure_pct=0.20,
        max_gross_leverage=1.0,
        max_open_positions=5,
        daily_loss_limit_pct=0.03,
        symbol_priority=[],
    )
    values.update(overrides)
    return PortfolioConfig(**values)


def _manager(state_path=None, **overrides) -> PortfolioRiskManager:
    return PortfolioRiskManager(
        _config(**overrides),
        PositionSizer(risk_pct=0.01),
        state_path=state_path,
    )


def _snapshot(equity: float = 10_000.0, session: date = SESSION) -> SessionEquitySnapshot:
    return SessionEquitySnapshot(
        session_date=session,
        equity=equity,
        taken_at=datetime(session.year, session.month, session.day, 9, 25, tzinfo=ET),
        account_id="DU1",
    )


def _position(symbol: str, side: str, qty: float, price: float) -> Position:
    return Position(
        symbol=symbol, side=side, quantity=qty, entry_price=price, current_price=price
    )


async def _executor(manager, capital: float = 10_000.0) -> tuple[TradeExecutor, MockExchange]:
    exchange = MockExchange(
        initial_capital=capital, commission_pct=0.0, partial_fill_probability=0.0
    )
    executor = TradeExecutor(
        exchange=exchange,
        order_manager=OrderManager(exchange=exchange),
        position_sizer=PositionSizer(risk_pct=0.01),
        risk_manager=manager,
    )
    return executor, exchange


# ---------------------------------------------------------------------------
# Exposure arithmetic
# ---------------------------------------------------------------------------

def test_oca_pair_reserves_the_larger_side_once():
    pending = [
        PendingExposure("AAA", 30, 100.0, group="oca-1"),
        PendingExposure("AAA", 40, 90.0, group="oca-1"),
        PendingExposure("BBB", 10, 50.0, group="ord-9"),
    ]
    assert reserved_pending_notional(pending) == pytest.approx(3_600 + 500)


def test_headroom_counts_long_short_and_pending_gross():
    positions = [
        OpenExposure("LONG", 100, 100.0),
        OpenExposure("SHORT", -50, 200.0),
    ]
    pending = [
        PendingExposure("PEND", 30, 100.0, group="oca-1"),
        PendingExposure("PEND", 40, 90.0, group="oca-1"),
    ]
    headroom = equity_headroom(
        current_equity=25_000.0,
        max_gross_leverage=1.0,
        positions=positions,
        pending=pending,
    )
    # A short is gross exposure too: it must not net against the long.
    assert headroom == pytest.approx(25_000 - 10_000 - 10_000 - 3_600)


def test_pre_trade_check_rejects_when_headroom_cannot_fund_one_share():
    manager = _manager()
    manager.record_snapshot(_snapshot(25_000.0))
    positions = {
        "LONG": _position("LONG", "long", 100, 100.0),
        "SHORT": _position("SHORT", "short", 50, 200.0),
    }
    pending = [
        PendingExposure("PEND", 30, 100.0, group="oca-1"),
        PendingExposure("PEND", 40, 90.0, group="oca-1"),
    ]

    blocked = manager.pre_trade_check(
        "NEW", "buy", 1, 1_500.0, 25_000.0, positions, pending_entries=pending
    )
    allowed = manager.pre_trade_check(
        "NEW", "buy", 1, 1_000.0, 25_000.0, positions, pending_entries=pending
    )

    assert not blocked.passed and "headroom" in blocked.reason
    assert allowed.passed


def test_pre_trade_check_ignores_the_entrys_own_oca_group():
    manager = _manager()
    manager.record_snapshot(_snapshot())
    own = [PendingExposure("AAA", 20, 100.0, group="oca-AAA")]

    result = manager.pre_trade_check(
        "AAA", "sell", 1, 99.0, 10_000.0, {}, pending_entries=own,
        exclude_pending_group="oca-AAA",
    )

    assert result.passed


# ---------------------------------------------------------------------------
# Cross-symbol position cap (regression)
# ---------------------------------------------------------------------------

def test_simple_risk_manager_cannot_see_other_symbols():
    """Documents the defect the portfolio manager fixes."""
    simple = SimpleRiskManager(PositionSizer(risk_pct=0.01), max_positions=1)
    # TradeExecutor only ever passes the current symbol's position.
    assert simple.pre_trade_check("BBB", "buy", 1, 100.0, 10_000.0, {}).passed


@pytest.mark.asyncio
async def test_open_position_cap_counts_every_symbol():
    manager = _manager(max_open_positions=1)
    manager.record_snapshot(_snapshot())
    executor, exchange = await _executor(manager)
    await exchange.set_price("AAA", 100.0)
    await exchange.set_price("BBB", 50.0)

    first = await executor.execute_signal("AAA", 1, 100.0, 98.0)
    second = await executor.execute_signal("BBB", 1, 50.0, 49.0)

    assert first.success
    assert not second.success
    assert "maximum positions" in second.reason


@pytest.mark.asyncio
async def test_open_position_cap_counts_pending_entries():
    manager = _manager(max_open_positions=2)
    manager.record_snapshot(_snapshot())
    executor, exchange = await _executor(manager)
    for symbol in ("AAA", "BBB", "CCC"):
        await exchange.set_price(symbol, 100.0)

    await executor.execute_signal("AAA", 1, 100.0, 98.0)
    pending = await executor.execute_signal(
        "BBB",
        1,
        101.0,
        99.0,
        entry_order=EntryOrderIntent(order_type="stop", trigger_price=101.0),
    )
    third = await executor.execute_signal("CCC", 1, 100.0, 99.0)

    assert pending.pending
    assert not third.success
    assert "maximum positions" in third.reason


# ---------------------------------------------------------------------------
# Session snapshot, persistence and blocking
# ---------------------------------------------------------------------------

def test_entries_blocked_until_snapshot_and_snapshot_reused_after_restart(tmp_path):
    path = tmp_path / "session_risk_state.json"
    manager = _manager(state_path=path)
    manager.begin_session(SESSION)
    assert manager.entries_blocked_reason() == SNAPSHOT_MISSING

    manager.record_snapshot(_snapshot(12_345.0))
    assert manager.entries_blocked_reason() is None

    restarted = _manager(state_path=path)
    restored = restarted.begin_session(SESSION)
    assert restored is not None and restored.equity == 12_345.0
    assert restarted.sizing_equity(99_999.0) == 12_345.0


def test_current_basis_does_not_need_a_snapshot():
    manager = _manager(sizing_equity_basis="current", daily_loss_limit_pct=None)
    manager.begin_session(SESSION)
    assert manager.entries_blocked_reason() is None
    assert manager.sizing_equity(8_000.0) == 8_000.0


@pytest.mark.asyncio
async def test_snapshot_failure_blocks_entries_and_alerts(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    sent = []

    class FakeNotifier:
        async def send_risk_alert(self, payload):
            sent.append(payload)
            return True

    @asynccontextmanager
    async def fake_context(_url):
        yield FakeNotifier()

    monkeypatch.setattr(warmup_module, "discord_notification_context", fake_context)

    class BrokenExchange:
        async def get_account(self):
            raise TimeoutError("account summary timed out")

    manager = _manager()
    orchestrator = SimpleNamespace(
        config=SimpleNamespace(
            notifications=SimpleNamespace(discord_webhook_url="https://discord.invalid/hook")
        ),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 9, 25), timezone="America/New_York"
        ),
        portfolio_risk_manager=manager,
        exchange=BrokenExchange(),
    )

    ok = await WarmupPhaseManager(orchestrator)._take_session_equity_snapshot()

    assert ok is False
    assert manager.entries_blocked_reason() == SNAPSHOT_FAILED
    assert [p.event_type for p in sent] == ["EQUITY_SNAPSHOT_FAILED"]
    assert "timed out" in sent[0].error

    executor, exchange = await _executor(manager)
    await exchange.set_price("AAA", 100.0)
    result = await executor.execute_signal("AAA", 1, 100.0, 99.0)
    assert not result.success
    assert SNAPSHOT_FAILED in result.reason


@pytest.mark.asyncio
async def test_warmup_snapshot_uses_net_liquidation_and_publishes_it():
    published = []

    class Exchange:
        calls = 0

        async def get_account(self):
            Exchange.calls += 1
            return SimpleNamespace(broker_equity=25_000.0, equity=25_000.0, account_id="DU1")

    async def persist(reason, account):
        published.append((reason, account.broker_equity))

    manager = _manager()
    orchestrator = SimpleNamespace(
        config=SimpleNamespace(notifications=SimpleNamespace(discord_webhook_url=None)),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 9, 25), timezone="America/New_York"
        ),
        portfolio_risk_manager=manager,
        exchange=Exchange(),
        _persist_dashboard_account_and_positions=persist,
    )
    phase = WarmupPhaseManager(orchestrator)

    assert await phase._take_session_equity_snapshot()
    assert await phase._take_session_equity_snapshot()  # intraday re-run

    assert manager.session_equity == 25_000.0
    assert Exchange.calls == 1
    assert published == [("session_start_equity", 25_000.0)]


# ---------------------------------------------------------------------------
# Daily loss limit
# ---------------------------------------------------------------------------

def test_daily_loss_trigger_and_reset_next_session():
    manager = _manager(daily_loss_limit_pct=0.03)
    manager.record_snapshot(_snapshot(10_000.0))

    assert not manager.check_daily_loss(9_701.0).triggered
    status = manager.check_daily_loss(9_700.0)
    assert status.triggered and status.limit == pytest.approx(-300.0)

    manager.halt(DAILY_LOSS_LIMIT, "test")
    assert manager.entries_blocked_reason() == DAILY_LOSS_LIMIT

    next_day = date(2026, 7, 21)
    manager.begin_session(next_day)
    assert manager.entries_blocked_reason() == SNAPSHOT_MISSING
    manager.record_snapshot(_snapshot(9_700.0, next_day))
    assert manager.entries_blocked_reason() is None


def _orchestrator(tmp_path) -> TradingOrchestrator:
    config = AppSettings(
        environment="test",
        database_path=str(tmp_path / "trades.db"),
        broker={"broker_type": "mock", "mode": "paper"},
        trading={"symbols": ["AAA", "BBB"]},
        dashboard={"enabled": False},
        operational_metrics={"enabled": False},
        notifications={"discord_webhook_url": "https://discord.invalid/hook"},
    )
    ruleset = SimpleNamespace(
        name="test_ruleset",
        version="1.0",
        instruments=SimpleNamespace(symbols=["AAA", "BBB"], timeframe="5m"),
    )
    return TradingOrchestrator(
        config=config,
        ruleset=ruleset,
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 11, 0), timezone="America/New_York"
        ),
        testing_mode=True,
    )


@pytest.mark.asyncio
async def test_daily_loss_limit_cancels_entries_flattens_blocks_and_alerts(
    tmp_path, monkeypatch
):
    import vibe.trading_bot.core.orchestrator as orchestrator_module

    sent = []

    class FakeNotifier:
        async def send_risk_alert(self, payload):
            sent.append(payload)
            return True

    @asynccontextmanager
    async def fake_context(_url):
        yield FakeNotifier()

    monkeypatch.setattr(orchestrator_module, "discord_notification_context", fake_context)

    orchestrator = _orchestrator(tmp_path)
    manager = _manager(daily_loss_limit_pct=0.03)
    manager.record_snapshot(_snapshot(10_000.0))
    executor, exchange = await _executor(manager)
    orchestrator.exchange = exchange
    orchestrator.trade_executor = executor
    orchestrator.portfolio_risk_manager = manager

    await exchange.set_price("AAA", 100.0)
    await exchange.set_price("BBB", 50.0)
    opened = await executor.execute_signal("AAA", 1, 100.0, 98.0)
    pending = await executor.execute_signal(
        "BBB", -1, 49.0, 51.0,
        entry_order=EntryOrderIntent(order_type="stop", trigger_price=49.0),
    )
    assert opened.success and pending.pending

    await orchestrator._enforce_account_risk_limits()
    assert manager.halted_reason is None  # no loss yet

    await exchange.set_price("AAA", 80.0)  # 20 shares (20% cap) x $20 = $400 > $300
    await orchestrator._enforce_account_risk_limits()

    assert manager.entries_blocked_reason() == DAILY_LOSS_LIMIT
    assert await executor.account_positions() == {}
    assert not executor.has_pending_entry("BBB")
    assert [p.event_type for p in sent] == ["DAILY_LOSS_LIMIT"]
    assert sent[0].flattened_symbols == ["AAA"]
    assert sent[0].cancelled_entries == 1

    blocked = await executor.execute_signal("BBB", 1, 50.0, 49.0)
    assert not blocked.success and DAILY_LOSS_LIMIT in blocked.reason

    await orchestrator._enforce_account_risk_limits()
    assert len(sent) == 1  # halted sessions are not re-alerted


def test_risk_alert_embed_follows_notification_standard():
    payload = RiskAlertPayload(
        event_type="DAILY_LOSS_LIMIT",
        timestamp=datetime(2026, 7, 20, 11, 0, tzinfo=ET),
        session_date="2026-07-20",
        message="limit",
        session_equity=10_000.0,
        current_equity=9_650.0,
        pnl=-350.0,
        limit=-300.0,
        cancelled_entries=1,
        flattened_symbols=["AAA"],
        version="v9.9.9",
    )
    embed = DiscordNotificationFormatter().format_risk_alert(payload)["embeds"][0]

    assert embed["color"] == 0xE74C3C
    assert embed["footer"]["text"] == "Trading Bot v9.9.9"
    assert embed["timestamp"] == payload.timestamp.isoformat()
    assert any(f["name"] == "Session P&L" for f in embed["fields"])

    with pytest.raises(ValueError):
        RiskAlertPayload(
            event_type="NOPE", timestamp=payload.timestamp, session_date="x", message="m"
        )


# ---------------------------------------------------------------------------
# Milestone A review fixes
# ---------------------------------------------------------------------------

def _capture_alerts(monkeypatch, module):
    sent = []

    class FakeNotifier:
        async def send_risk_alert(self, payload):
            sent.append(payload)
            return True

    @asynccontextmanager
    async def fake_context(_url):
        yield FakeNotifier()

    monkeypatch.setattr(module, "discord_notification_context", fake_context)
    return sent


class NoNetLiquidationExchange(MockExchange):
    """IB reported no NetLiquidation: AccountState.equity is clamped to 0."""

    async def get_account(self):
        account = await super().get_account()
        return account.model_copy(update={"broker_equity": None, "equity": 0.0})


async def _wired_orchestrator(tmp_path, monkeypatch, exchange_cls=MockExchange, **config):
    import vibe.trading_bot.core.orchestrator as orchestrator_module

    sent = _capture_alerts(monkeypatch, orchestrator_module)
    orchestrator = _orchestrator(tmp_path)
    manager = _manager(**config)
    manager.record_snapshot(_snapshot(10_000.0))
    exchange = exchange_cls(initial_capital=10_000.0, commission_pct=0.0, partial_fill_probability=0.0)
    executor = TradeExecutor(
        exchange=exchange,
        order_manager=OrderManager(exchange=exchange),
        position_sizer=PositionSizer(risk_pct=0.01),
        risk_manager=manager,
    )
    orchestrator.exchange = exchange
    orchestrator.trade_executor = executor
    orchestrator.portfolio_risk_manager = manager
    return orchestrator, manager, executor, exchange, sent


@pytest.mark.asyncio
async def test_missing_net_liquidation_never_triggers_the_daily_loss_limit(tmp_path, monkeypatch):
    orchestrator, manager, executor, exchange, sent = await _wired_orchestrator(
        tmp_path, monkeypatch, exchange_cls=NoNetLiquidationExchange
    )
    await exchange.set_price("AAA", 100.0)
    await exchange.submit_order("AAA", "buy", 10, order_type="market", price=100.0)

    await orchestrator._enforce_account_risk_limits()

    assert manager.halted_reason is None
    assert sent == []
    assert "AAA" in await executor.account_positions()
    blocked = await executor.execute_signal("AAA", -1, 100.0, 101.0)
    assert not blocked.success and RISK_EVALUATION_FAILED in blocked.reason

    # Independently of the evaluation flag, the executor refuses to size
    # without NetLiquidation.
    manager.evaluation_error = None
    blocked = await executor.execute_signal("AAA", -1, 100.0, 101.0)
    assert not blocked.success and NET_LIQUIDATION_UNAVAILABLE in blocked.reason


def test_daily_loss_ignores_non_positive_equity():
    manager = _manager()
    manager.record_snapshot(_snapshot(10_000.0))
    assert not manager.check_daily_loss(0.0).triggered
    assert not manager.check_daily_loss(None).triggered


@pytest.mark.asyncio
async def test_risk_evaluation_errors_fail_closed_without_aborting(tmp_path, monkeypatch):
    orchestrator, manager, executor, exchange, _sent = await _wired_orchestrator(
        tmp_path, monkeypatch
    )
    manager.halt(DAILY_LOSS_LIMIT, "earlier breach")

    async def broken_positions():
        raise TimeoutError("IB positions timed out")

    monkeypatch.setattr(executor, "account_positions", broken_positions)

    await orchestrator._enforce_account_risk_limits()  # must not raise
    assert manager.evaluation_error is not None

    manager.halted_reason = None
    assert manager.entries_blocked_reason() == RISK_EVALUATION_FAILED

    monkeypatch.undo()
    await orchestrator._enforce_account_risk_limits()
    assert manager.evaluation_error is None
    assert manager.entries_blocked_reason() is None


@pytest.mark.asyncio
async def test_empty_timeout_blocks_readiness_alerts_once_and_recovers(tmp_path, monkeypatch):
    from vibe.trading_bot.api.health import HealthState, get_health_state

    monkeypatch.setattr("vibe.trading_bot.api.health._health_state", HealthState())
    orchestrator, manager, _, exchange, _ = await _wired_orchestrator(tmp_path, monkeypatch)
    notify = AsyncMock()
    monkeypatch.setattr(orchestrator, "_send_operator_alert", notify)
    healthy_get_account = exchange.get_account
    monkeypatch.setattr(exchange, "get_account", AsyncMock(side_effect=TimeoutError()))

    await orchestrator._enforce_account_risk_limits()
    await orchestrator._enforce_account_risk_limits()
    assert manager.entries_blocked_reason() == RISK_EVALUATION_FAILED
    assert "TimeoutError" in manager.evaluation_error
    assert "account_risk" in get_health_state().readiness_blockers
    assert notify.await_count == 1

    monkeypatch.setattr(exchange, "get_account", healthy_get_account)
    await orchestrator._enforce_account_risk_limits()
    assert manager.evaluation_error is None
    assert "account_risk" not in get_health_state().readiness_blockers
    monkeypatch.setattr(exchange, "get_account", AsyncMock(side_effect=TimeoutError()))
    await orchestrator._enforce_account_risk_limits()
    assert notify.await_count == 2


@pytest.mark.asyncio
async def test_upstream_loss_needs_restoration_and_fresh_account_before_ready(tmp_path, monkeypatch):
    from vibe.trading_bot.api.health import HealthState, get_health_state

    monkeypatch.setattr("vibe.trading_bot.api.health._health_state", HealthState())
    orchestrator, manager, _, exchange, _ = await _wired_orchestrator(tmp_path, monkeypatch)
    monkeypatch.setattr(orchestrator, "_send_operator_alert", AsyncMock())
    healthy_get_account = exchange.get_account
    orchestrator._on_ib_connectivity_error(-1, 1100, "upstream lost")
    assert not orchestrator._entry_dependencies_available("AAA")
    await orchestrator._enforce_account_risk_limits()
    assert "ib_upstream" in get_health_state().readiness_blockers
    assert manager.entries_blocked_reason() == RISK_EVALUATION_FAILED
    orchestrator._on_ib_connectivity_error(-1, 1102, "upstream restored")
    assert not orchestrator._entry_dependencies_available("AAA")
    monkeypatch.setattr(exchange, "get_account", AsyncMock(side_effect=TimeoutError()))
    await orchestrator._enforce_account_risk_limits()
    assert "ib_upstream" in get_health_state().readiness_blockers
    monkeypatch.setattr(exchange, "get_account", healthy_get_account)
    await orchestrator._enforce_account_risk_limits()
    assert not get_health_state().readiness_blockers
    assert orchestrator._entry_dependencies_available("AAA")


@pytest.mark.asyncio
async def test_trading_cycle_still_monitors_exits_when_risk_evaluation_fails(tmp_path, monkeypatch):
    orchestrator, manager, executor, exchange, _sent = await _wired_orchestrator(
        tmp_path, monkeypatch
    )

    async def broken_get_account():
        raise TimeoutError("account summary timed out")

    monitored = []

    async def monitor():
        monitored.append(True)

    async def noop():
        return None

    orchestrator.data_manager = SimpleNamespace()
    orchestrator.active_provider = None
    orchestrator.ruleset.instruments.symbols = []
    monkeypatch.setattr(exchange, "get_account", broken_get_account)
    monkeypatch.setattr(orchestrator, "_monitor_open_positions", monitor)
    monkeypatch.setattr(orchestrator, "_check_and_send_orb_notification", noop)
    monkeypatch.setattr(orchestrator, "_flush_elapsed_bars", noop)

    await orchestrator._trading_cycle()

    assert monitored == [True]
    assert manager.evaluation_error is not None


@pytest.mark.parametrize(
    "content",
    [
        "[1, 2, 3]",
        '{"session_date": "TODAY", "equity": 5000, "taken_at": null}',
        '{"session_date": "TODAY", "equity": "abc"}',
        "{not json",
    ],
)
def test_corrupt_state_written_today_fails_closed_and_alerts_once(tmp_path, content):
    today = datetime.now(ET).date()
    path = tmp_path / "session_risk_state.json"
    path.write_text(content.replace("TODAY", today.isoformat()), encoding="utf-8")
    manager = _manager(state_path=path)

    assert manager.begin_session(today) is None  # never raises
    assert manager.entries_blocked_reason() == SNAPSHOT_FAILED
    assert manager.take_unsent_failure_alert() is True
    assert manager.take_unsent_failure_alert() is False

    restarted = _manager(state_path=path)
    restarted.begin_session(today)
    assert restarted.entries_blocked_reason() == SNAPSHOT_FAILED
    assert restarted.take_unsent_failure_alert() is False


def test_corrupt_state_from_an_earlier_day_is_ignored(tmp_path):
    import os

    path = tmp_path / "session_risk_state.json"
    path.write_text("{not json", encoding="utf-8")
    old = datetime(2026, 7, 17, 12, 0, tzinfo=ET).timestamp()
    os.utime(path, (old, old))
    manager = _manager(state_path=path)

    manager.begin_session(SESSION)

    assert manager.halted_reason is None
    assert manager.entries_blocked_reason() == SNAPSHOT_MISSING


@pytest.mark.asyncio
async def test_pending_fill_missing_from_positions_snapshot_stays_reserved():
    manager = _manager(max_open_positions=2)
    manager.record_snapshot(_snapshot())
    executor, exchange = await _executor(manager)
    await exchange.set_price("BBB", 100.0)
    pending = await executor.execute_signal(
        "BBB", 1, 101.0, 99.0,
        entry_order=EntryOrderIntent(order_type="stop", trigger_price=101.0),
    )
    assert pending.pending
    order_id = pending.order_id
    reserved = executor.pending_exposures({})[0].notional

    # The stop fills after the caller's positions snapshot was taken.
    executor.order_manager.get_order(order_id).filled_qty = float(
        executor.get_pending_entry(order_id)["quantity"]
    )

    stale_snapshot = executor.pending_exposures({})
    assert [p.symbol for p in stale_snapshot] == ["BBB"]
    assert stale_snapshot[0].notional == pytest.approx(reserved)

    bbb = _position("BBB", "long", 10, 101.0)
    assert executor.pending_exposures({"BBB": bbb}) == []


@pytest.mark.asyncio
async def test_halted_flatten_skips_symbols_with_a_close_in_flight(tmp_path, monkeypatch):
    orchestrator, manager, executor, exchange, sent = await _wired_orchestrator(
        tmp_path, monkeypatch
    )
    await exchange.set_price("AAA", 100.0)
    await exchange.submit_order("AAA", "buy", 10, order_type="market", price=100.0)
    manager.halt(DAILY_LOSS_LIMIT, "earlier breach")
    executor._pending_closes["AAA"] = "close-1"
    attempts = []

    async def record_close(symbol, **kwargs):
        attempts.append(symbol)
        raise AssertionError("must not resubmit a close in flight")

    monkeypatch.setattr(executor, "_close_position", record_close)

    for _ in range(3):
        await orchestrator._enforce_account_risk_limits()

    assert attempts == []
    assert manager.evaluation_error is None
    assert sent == []


def _warmup_orchestrator(exchange, manager, hhmm=(9, 31), trade_executor=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            notifications=SimpleNamespace(discord_webhook_url="https://discord.invalid/hook")
        ),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, *hhmm), timezone="America/New_York"
        ),
        portfolio_risk_manager=manager,
        exchange=exchange,
        trade_executor=trade_executor,
    )


def _flat_mock() -> MockExchange:
    return MockExchange(initial_capital=10_000.0, commission_pct=0.0, partial_fill_probability=0.0)


@pytest.mark.asyncio
async def test_slow_warmup_crossing_the_open_with_a_flat_book_takes_a_late_snapshot(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    sent = _capture_alerts(monkeypatch, warmup_module)
    manager = _manager()
    phase = WarmupPhaseManager(_warmup_orchestrator(_flat_mock(), manager, (9, 31)))

    assert await phase._take_session_equity_snapshot() is True

    assert manager.session_equity == 10_000.0
    assert manager.snapshot.late is True
    assert manager.snapshot.taken_at.hour == 9 and manager.snapshot.taken_at.minute == 31
    assert manager.entries_blocked_reason() is None
    assert sent == []


@pytest.mark.asyncio
async def test_late_snapshot_flag_survives_a_restart(tmp_path):
    path = tmp_path / "session_risk_state.json"
    manager = _manager(state_path=path)
    phase = WarmupPhaseManager(_warmup_orchestrator(_flat_mock(), manager, (10, 0)))
    assert await phase._take_session_equity_snapshot() is True

    restarted = _manager(state_path=path)
    restored = restarted.begin_session(SESSION)
    assert restored.late is True and restored.equity == 10_000.0


@pytest.mark.asyncio
async def test_restart_with_an_open_position_takes_no_late_snapshot_and_alerts_once(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    sent = _capture_alerts(monkeypatch, warmup_module)
    exchange = _flat_mock()
    await exchange.set_price("AAA", 100.0)
    await exchange.submit_order("AAA", "buy", 10, order_type="market", price=100.0)
    manager = _manager()
    phase = WarmupPhaseManager(_warmup_orchestrator(exchange, manager, (10, 30)))

    assert await phase._take_session_equity_snapshot() is False
    assert await phase._take_session_equity_snapshot() is False

    assert manager.session_equity is None
    assert manager.entries_blocked_reason() == SNAPSHOT_FAILED
    assert "open positions: AAA" in manager.halt_detail
    assert [p.event_type for p in sent] == ["EQUITY_SNAPSHOT_FAILED"]


@pytest.mark.asyncio
async def test_restart_after_a_fill_today_takes_no_late_snapshot(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    sent = _capture_alerts(monkeypatch, warmup_module)
    exchange = _flat_mock()
    await exchange.set_price("AAA", 100.0)
    await exchange.submit_order("AAA", "buy", 10, order_type="market", price=100.0)
    await exchange.submit_order("AAA", "sell", 10, order_type="market", price=100.0)
    assert await exchange.list_positions() == []  # flat, but it traded today
    manager = _manager()
    # Executions are stamped with the real clock, so use today's market date.
    now = datetime.now(ET)
    orchestrator = _warmup_orchestrator(exchange, manager, (10, 30))
    orchestrator.market_scheduler = MockMarketScheduler(
        initial_date=datetime(now.year, now.month, now.day, 23, 59),
        timezone="America/New_York",
    )

    assert await WarmupPhaseManager(orchestrator)._take_session_equity_snapshot() is False
    assert "execution(s) today" in manager.halt_detail
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_restart_with_only_cancelled_orders_today_takes_a_late_snapshot(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    sent = _capture_alerts(monkeypatch, warmup_module)
    exchange = _flat_mock()
    await exchange.set_price("AAA", 100.0)
    resting = await exchange.submit_order(
        "AAA", "buy", 10, order_type="stop", price=105.0, stop_price=105.0
    )
    await exchange.cancel_order(resting.order_id)
    manager = _manager()

    phase = WarmupPhaseManager(_warmup_orchestrator(exchange, manager, (10, 30)))

    assert await phase._take_session_equity_snapshot() is True
    assert manager.snapshot.late is True
    assert sent == []


@pytest.mark.asyncio
async def test_restart_with_a_working_order_takes_no_late_snapshot(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    _capture_alerts(monkeypatch, warmup_module)
    exchange = _flat_mock()
    await exchange.set_price("AAA", 100.0)
    await exchange.submit_order("AAA", "buy", 10, order_type="stop", price=105.0, stop_price=105.0)
    manager = _manager()

    phase = WarmupPhaseManager(_warmup_orchestrator(exchange, manager, (10, 30)))

    assert await phase._take_session_equity_snapshot() is False
    assert "working orders" in manager.halt_detail


@pytest.mark.asyncio
async def test_skipped_daily_loss_evaluation_keeps_entries_blocked(tmp_path, monkeypatch):
    orchestrator, manager, executor, exchange, sent = await _wired_orchestrator(
        tmp_path, monkeypatch, exchange_cls=NoNetLiquidationExchange
    )

    await orchestrator._enforce_account_risk_limits()

    assert manager.evaluation_error == NET_LIQUIDATION_UNAVAILABLE
    assert manager.entries_blocked_reason() == RISK_EVALUATION_FAILED

    orchestrator.exchange = MockExchange(
        initial_capital=10_000.0, commission_pct=0.0, partial_fill_probability=0.0
    )
    await orchestrator._enforce_account_risk_limits()
    assert manager.evaluation_error is None

@pytest.mark.asyncio
async def test_warmup_snapshot_rejects_missing_net_liquidation(monkeypatch):
    import vibe.trading_bot.core.phases.warmup as warmup_module

    sent = _capture_alerts(monkeypatch, warmup_module)

    class Exchange:
        async def get_account(self):
            return SimpleNamespace(broker_equity=None, equity=0.0, account_id="DU1")

    manager = _manager()
    orchestrator = SimpleNamespace(
        config=SimpleNamespace(
            notifications=SimpleNamespace(discord_webhook_url="https://discord.invalid/hook")
        ),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 9, 25), timezone="America/New_York"
        ),
        portfolio_risk_manager=manager,
        exchange=Exchange(),
    )

    assert await WarmupPhaseManager(orchestrator)._take_session_equity_snapshot() is False
    assert manager.entries_blocked_reason() == SNAPSHOT_FAILED
    assert len(sent) == 1
