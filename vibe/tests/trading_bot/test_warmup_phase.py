"""Tests for trading bot warmup phase behavior."""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from vibe.common.models import Order, Position
from vibe.common.risk import PositionSizer
from vibe.common.ruleset.models import PortfolioConfig
from vibe.trading_bot.brokers.base import BrokerAccount, BrokerQuote
from vibe.trading_bot.core.phases.warmup import WarmupPhaseManager
from vibe.trading_bot.data.providers.interactive_brokers import InteractiveBrokersDataProvider
from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine
from vibe.trading_bot.execution.portfolio_risk import (
    DAILY_LOSS_LIMIT,
    PortfolioRiskManager,
    SessionEquitySnapshot,
)
from vibe.trading_bot.execution.trade_executor import ExecutionResult


class FakeExchange:
    async def get_position(self, symbol):
        return Position(
            symbol=symbol,
            side="short",
            quantity=1,
            entry_price=100.0,
            current_price=101.0,
        )


class FakeTradeExecutor:
    def __init__(self):
        self.cancel_after_seconds = None

    async def _close_position(self, symbol, cancel_after_seconds=None):
        self.cancel_after_seconds = cancel_after_seconds
        return ExecutionResult(
            success=True,
            order_id="close-1",
            reason="closed",
            position_size=1,
            avg_price=101.0,
        )


@pytest.mark.asyncio
async def test_carryover_flatten_uses_extended_order_timeout():
    trade_executor = FakeTradeExecutor()
    orchestrator = SimpleNamespace(
        config=SimpleNamespace(
            strategy=SimpleNamespace(carryover_position_policy="flatten_at_market_open")
        ),
        active_symbols=["QQQ"],
        exchange=FakeExchange(),
        trade_executor=trade_executor,
    )
    manager = WarmupPhaseManager(orchestrator)

    result = await manager._apply_carryover_position_policy(send_notification=True)

    assert result is True
    assert trade_executor.cancel_after_seconds == manager.CARRYOVER_FLATTEN_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_broker_health_failure_sends_critical_discord_alert(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module
    import vibe.trading_bot.core.phases.warmup as warmup_module

    class FailingBroker:
        def __init__(self, **kwargs):
            pass

        async def connect(self):
            raise ib_module.IBConnectionFailed("failed after 3 attempts")

        async def disconnect(self):
            return True

    sent_payloads = []

    class FakeNotifier:
        async def send_system_alert(self, payload):
            sent_payloads.append(payload)
            return True

    @asynccontextmanager
    async def fake_notification_context(webhook_url):
        assert webhook_url == "https://discord.example/webhook"
        yield FakeNotifier()

    monkeypatch.setattr(
        warmup_module,
        "discord_notification_context",
        fake_notification_context,
    )

    orchestrator = SimpleNamespace(
        config=SimpleNamespace(
            broker=SimpleNamespace(
                broker_type="interactive_brokers",
                health_check_enabled=True,
                ib_host="127.0.0.1",
                ib_port=4002,
                ib_client_id=201,
                ib_account_id="DU123",
                ib_exchange="SMART",
                ib_currency="USD",
                ib_market_data_type=1,
                ib_connect_timeout=0.01,
                ib_connect_max_retries=3,
                ib_connect_retry_delay_seconds=0,
            ),
            notifications=SimpleNamespace(
                discord_webhook_url="https://discord.example/webhook",
            ),
        ),
        exchange=SimpleNamespace(broker=FailingBroker()),
        active_symbols=["QQQ"],
        market_scheduler=SimpleNamespace(timezone=None),
    )
    manager = WarmupPhaseManager(orchestrator)
    monkeypatch.setattr(
        warmup_module,
        "get_market_now",
        lambda scheduler: warmup_module.datetime(2026, 10, 9, 9, 25),
    )

    assert await manager._verify_broker_health() is False
    assert await manager._verify_broker_health() is False
    assert len(sent_payloads) == 1
    alert = sent_payloads[0]
    assert alert.severity == "critical"
    assert alert.component == "interactive_brokers"
    assert alert.details["attempts"] == 3


@pytest.fixture
def reconnecting_warmup(monkeypatch, tmp_path):
    now = datetime(2026, 11, 10, 9, 25, tzinfo=ZoneInfo("America/New_York"))
    socket = SimpleNamespace(connected=False)
    events = []

    async def reconnect():
        events.append("execution_connect")
        socket.connected = True
        return True

    async def account_info():
        events.append("account")
        if not socket.connected:
            raise ConnectionError("Not connected")
        return BrokerAccount(
            account_id="DU123",
            net_liquidation=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            available_funds=10_000.0,
        )

    broker = SimpleNamespace(
        ib=SimpleNamespace(isConnected=lambda: socket.connected),
        connect=AsyncMock(side_effect=reconnect),
        disconnect=AsyncMock(),
        get_account_info=AsyncMock(side_effect=account_info),
        get_market_data=AsyncMock(return_value=BrokerQuote(
            symbol="QQQ", bid=99.99, ask=100.01, last=100.0,
            market_price=100.0, timestamp=now,
        )),
        get_positions=AsyncMock(return_value=[]),
    )
    exchange = InteractiveBrokersExecutionEngine(broker)
    existing_order = Order(
        order_id="existing", symbol="QQQ", side="buy", quantity=1, price=100.0
    )
    exchange._orders[existing_order.order_id] = existing_order
    risk = PortfolioRiskManager(
        PortfolioConfig(
            sizing_equity_basis="session_start",
            max_symbol_exposure_pct=0.20,
            max_gross_leverage=1.0,
            max_open_positions=5,
            daily_loss_limit_pct=0.03,
            symbol_priority=[],
        ),
        PositionSizer(risk_pct=0.01),
        state_path=tmp_path / "session_risk_state.json",
    )
    primary_provider = InteractiveBrokersDataProvider(
        broker, owns_broker_lifecycle=False
    )
    orchestrator = SimpleNamespace(
        config=SimpleNamespace(
            broker=SimpleNamespace(
                broker_type="interactive_brokers",
                health_check_enabled=True,
                health_check_symbol="QQQ",
                ib_account_id="DU123",
                ib_connect_max_retries=3,
            ),
            strategy=SimpleNamespace(carryover_position_policy="block_new_entries"),
            notifications=SimpleNamespace(discord_webhook_url=None),
        ),
        exchange=exchange,
        trade_executor=SimpleNamespace(risk_manager=risk),
        portfolio_risk_manager=risk,
        primary_provider=primary_provider,
        secondary_provider=None,
        active_symbols=["QQQ"],
        market_scheduler=SimpleNamespace(
            timezone=now.tzinfo,
            get_open_time=lambda *args: now.replace(minute=30),
        ),
        _persist_dashboard_account_and_positions=AsyncMock(),
    )
    manager = WarmupPhaseManager(orchestrator)
    manager._cleanup_stale_data = Mock()
    manager._prefetch_historical_data = AsyncMock(return_value=True)
    manager._verify_bar_aggregators = AsyncMock(return_value=True)
    manager._precalculate_indicators = AsyncMock()
    manager._run_health_checks = AsyncMock(return_value=({}, True))
    manager._send_critical_broker_alert = AsyncMock()
    manager._send_risk_alert = AsyncMock()
    manager._take_session_equity_snapshot = AsyncMock(
        wraps=manager._take_session_equity_snapshot
    )
    manager._apply_carryover_position_policy = AsyncMock(
        wraps=manager._apply_carryover_position_policy
    )
    monkeypatch.setattr(
        "vibe.trading_bot.core.phases.warmup.get_market_now", lambda scheduler: now
    )
    monkeypatch.setattr(
        "vibe.trading_bot.brokers.interactive_brokers.InteractiveBrokersAPI",
        Mock(side_effect=AssertionError("Must reuse the trading client")),
    )
    return SimpleNamespace(
        manager=manager, orchestrator=orchestrator, broker=broker,
        socket=socket, risk=risk, events=events,
        existing_order=existing_order, now=now, reconnect=reconnect,
    )


@pytest.mark.asyncio
async def test_warmup_reconnects_trading_client_before_account_reads(reconnecting_warmup):
    case = reconnecting_warmup
    executor = case.orchestrator.trade_executor

    assert await case.manager.execute(send_notification=False) is True

    case.broker.connect.assert_awaited_once()
    case.broker.disconnect.assert_not_awaited()
    assert case.orchestrator.primary_provider.broker is case.orchestrator.exchange.broker
    assert case.orchestrator.primary_provider.connected
    assert case.events == ["execution_connect", "account", "account"]
    assert case.socket.connected
    assert case.risk.snapshot.equity == 10_000.0
    assert case.risk.snapshot.late is False
    assert case.risk.halted_reason is None
    assert case.orchestrator.exchange._orders["existing"] is case.existing_order
    assert case.orchestrator.trade_executor is executor


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "false", "market_data"])
async def test_failed_recovery_defers_equity_snapshot_then_retries(
    reconnecting_warmup, failure
):
    case = reconnecting_warmup
    if failure == "exception":
        case.broker.connect.side_effect = ConnectionError("Gateway restarting")
    elif failure == "false":
        case.broker.connect.side_effect = None
        case.broker.connect.return_value = False
    else:
        case.broker.get_market_data.side_effect = TimeoutError("Quote unavailable")

    assert await case.manager.execute(send_notification=False) is False

    case.manager._apply_carryover_position_policy.assert_not_awaited()
    case.manager._take_session_equity_snapshot.assert_not_awaited()
    case.manager._send_critical_broker_alert.assert_awaited_once()
    assert case.risk.snapshot is None
    assert case.risk.halted_reason is None
    assert not case.risk.state_path.exists()

    case.broker.connect.side_effect = case.reconnect
    case.broker.get_market_data.side_effect = None
    assert await case.manager.execute(send_notification=False) is True
    assert case.risk.snapshot.equity == 10_000.0
    assert case.risk.state_path.is_file()
    assert case.risk.halted_reason is None
    case.broker.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_health_checks_still_reconnect_trading_client(reconnecting_warmup):
    case = reconnecting_warmup
    case.orchestrator.config.broker.health_check_enabled = False

    assert await case.manager.execute(send_notification=False) is True

    case.broker.connect.assert_awaited_once()
    case.broker.get_market_data.assert_not_awaited()
    case.broker.get_positions.assert_not_awaited()
    assert case.events == ["execution_connect", "account"]
    assert case.risk.snapshot is not None


@pytest.mark.asyncio
async def test_reconnect_preserves_existing_equity_and_daily_halt(reconnecting_warmup):
    case = reconnecting_warmup
    snapshot = SessionEquitySnapshot(
        session_date=case.now.date(), equity=9_000.0, taken_at=case.now
    )
    case.risk.record_snapshot(snapshot)
    case.risk.halted_reason = DAILY_LOSS_LIMIT

    assert await case.manager.execute(send_notification=False) is True

    case.broker.connect.assert_awaited_once()
    assert case.risk.snapshot is snapshot
    assert case.risk.halted_reason == DAILY_LOSS_LIMIT
    assert case.events == ["execution_connect", "account"]


@pytest.mark.asyncio
async def test_non_ib_health_check_does_not_reconnect_exchange(reconnecting_warmup):
    case = reconnecting_warmup
    case.orchestrator.config.broker.broker_type = "mock"

    assert await case.manager._verify_broker_health() is True

    case.broker.connect.assert_not_awaited()