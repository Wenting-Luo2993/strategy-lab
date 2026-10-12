"""Tests for trading bot warmup phase behavior."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from vibe.common.models import Position
from vibe.trading_bot.core.phases.warmup import WarmupPhaseManager
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

    monkeypatch.setattr(ib_module, "InteractiveBrokersAPI", FailingBroker)
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
    assert len(sent_payloads) == 1
    alert = sent_payloads[0]
    assert alert.severity == "critical"
    assert alert.component == "interactive_brokers"
    assert alert.details["attempts"] == 3