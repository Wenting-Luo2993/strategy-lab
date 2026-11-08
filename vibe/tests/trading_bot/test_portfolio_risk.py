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

    await exchange.set_price("AAA", 93.0)  # 20 x 100 = $2,000 at risk; loss 7%
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
