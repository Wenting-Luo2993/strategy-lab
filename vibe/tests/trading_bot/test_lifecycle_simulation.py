"""Compressed lifecycle simulation tests for the trading bot."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.trading_bot.exchange.ib_exchange import InteractiveBrokersExecutionEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_succeeds", [True, False])
async def test_trading_client_disconnect_requires_recovery_with_healthy_data_client(
    monkeypatch, recovery_succeeds
):
    scheduler = MockMarketScheduler(
        initial_date=datetime(2026, 7, 15, 10, 0),
        timezone="America/New_York",
    )
    orchestrator = TradingOrchestrator(
        config=AppSettings(
            environment="test",
            database_path=":memory:",
            health_check_port=0,
            trading={"symbols": ["QQQ"]},
            data={"primary_provider": "finnhub"},
            broker={"broker_type": "mock"},
        ),
        market_scheduler=scheduler,
        testing_mode=True,
    )
    socket = SimpleNamespace(connected=False)
    broker = SimpleNamespace(ib=SimpleNamespace(isConnected=lambda: socket.connected))
    orchestrator.exchange = InteractiveBrokersExecutionEngine(broker)
    orchestrator.primary_provider = SimpleNamespace(connected=True)
    orchestrator._warmup_ready = True
    events = []

    async def recover(send_notification=True):
        assert send_notification is False
        events.append("warmup")
        socket.connected = recovery_succeeds
        if not recovery_succeeds:
            orchestrator._shutdown_event.set()
        return recovery_succeeds

    async def trade():
        assert socket.connected
        events.append("trading")
        orchestrator._shutdown_event.set()
        return True

    orchestrator.warmup_manager = SimpleNamespace(execute=recover)
    monkeypatch.setattr(orchestrator, "initialize", AsyncMock(return_value=True))
    monkeypatch.setattr(orchestrator, "_trading_cycle", trade)
    monkeypatch.setattr(orchestrator, "_calculate_sleep_interval", lambda: 0)
    monkeypatch.setattr(orchestrator, "shutdown", AsyncMock())
    monkeypatch.setattr("vibe.trading_bot.core.orchestrator.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "vibe.trading_bot.core.orchestrator.start_health_server_task",
        AsyncMock(return_value=None),
    )

    await orchestrator.run()

    assert events == (["warmup", "trading"] if recovery_succeeds else ["warmup"])
    assert orchestrator._warmup_ready is recovery_succeeds


@pytest.mark.asyncio
async def test_compressed_multi_day_warmup_market_cooldown_cycle(monkeypatch):
    scheduler = MockMarketScheduler(
        initial_date=datetime(2026, 7, 15, 9, 25),
        timezone="America/New_York",
    )
    config = AppSettings(
        environment="test",
        database_path=":memory:",
        health_check_port=0,
        trading={"symbols": ["QQQ"]},
        data={"primary_provider": "finnhub"},
        broker={"broker_type": "mock"},
    )
    orchestrator = TradingOrchestrator(
        config=config,
        market_scheduler=scheduler,
        testing_mode=True,
    )
    provider = SimpleNamespace(connected=True)
    events: list[str] = []

    class FakeWarmupManager:
        async def execute(self, send_notification: bool = True) -> bool:
            events.append(f"warmup:{scheduler.now().date()}:{send_notification}")
            provider.connected = True
            if len([event for event in events if event.startswith("warmup:")]) == 2:
                orchestrator._shutdown_event.set()
            return True

    class FakeCooldownManager:
        def __init__(self):
            self.executions = 0

        def reset(self) -> None:
            events.append(f"cooldown_reset:{scheduler.now().date()}")
            self.executions = 0

        async def execute(self) -> None:
            self.executions += 1
            events.append(f"cooldown:{scheduler.now().date()}:{self.executions}")
            if self.executions >= 2:
                provider.connected = False
                scheduler.set_date(2026, 7, 16, 9, 25)

        def is_cooldown_complete(self) -> bool:
            return self.executions >= 2

        def calculate_sleep_until_warmup(self) -> float:
            return 0

        def should_log_sleep_message(self) -> bool:
            return False

    async def fake_initialize() -> bool:
        orchestrator.active_provider = provider
        orchestrator.primary_provider = provider
        orchestrator.warmup_manager = FakeWarmupManager()
        orchestrator.cooldown_manager = FakeCooldownManager()
        return True

    async def fake_trading_cycle() -> bool:
        events.append(f"trading:{scheduler.now().date()}:{scheduler.now().time()}")
        scheduler.set_time(16, 0)
        return True

    async def fake_sleep(seconds: float) -> None:
        if scheduler.is_warmup_phase():
            scheduler.set_time(9, 30)

    async def fake_start_health_server_task(*args, **kwargs):
        return None

    async def fake_shutdown() -> None:
        orchestrator._running = False

    monkeypatch.setattr(orchestrator, "initialize", fake_initialize)
    monkeypatch.setattr(orchestrator, "_trading_cycle", fake_trading_cycle)
    monkeypatch.setattr(orchestrator, "_calculate_sleep_interval", lambda: 0)
    monkeypatch.setattr(orchestrator, "shutdown", fake_shutdown)
    monkeypatch.setattr("vibe.trading_bot.core.orchestrator.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "vibe.trading_bot.core.orchestrator.start_health_server_task",
        fake_start_health_server_task,
    )

    await orchestrator.run()

    assert events == [
        "cooldown_reset:2026-07-15",
        "warmup:2026-07-15:True",
        "trading:2026-07-15:09:30:00",
        "cooldown:2026-07-15:1",
        "cooldown:2026-07-15:2",
        "cooldown_reset:2026-07-16",
        "warmup:2026-07-16:True",
    ]


@pytest.mark.asyncio
async def test_failed_warmup_blocks_market_hours_trading(monkeypatch):
    scheduler = MockMarketScheduler(
        initial_date=datetime(2026, 7, 15, 9, 25),
        timezone="America/New_York",
    )
    config = AppSettings(
        environment="test",
        database_path=":memory:",
        health_check_port=0,
        trading={"symbols": ["QQQ"]},
        data={"primary_provider": "finnhub"},
        broker={"broker_type": "mock"},
    )
    orchestrator = TradingOrchestrator(
        config=config,
        market_scheduler=scheduler,
        testing_mode=True,
    )
    provider = SimpleNamespace(connected=True)
    events: list[str] = []

    class FakeWarmupManager:
        async def execute(self, send_notification: bool = True) -> bool:
            events.append(f"warmup:{send_notification}")
            if not send_notification:
                orchestrator._shutdown_event.set()
            return False

    class FakeCooldownManager:
        def reset(self) -> None:
            events.append("cooldown_reset")

    async def fake_initialize() -> bool:
        orchestrator.active_provider = provider
        orchestrator.primary_provider = provider
        orchestrator.warmup_manager = FakeWarmupManager()
        orchestrator.cooldown_manager = FakeCooldownManager()
        return True

    async def fake_trading_cycle() -> bool:
        events.append("trading")
        return True

    async def fake_sleep(seconds: float) -> None:
        if scheduler.is_warmup_phase():
            scheduler.set_time(9, 30)

    async def fake_start_health_server_task(*args, **kwargs):
        return None

    async def fake_shutdown() -> None:
        orchestrator._running = False

    monkeypatch.setattr(orchestrator, "initialize", fake_initialize)
    monkeypatch.setattr(orchestrator, "_trading_cycle", fake_trading_cycle)
    monkeypatch.setattr(orchestrator, "shutdown", fake_shutdown)
    monkeypatch.setattr("vibe.trading_bot.core.orchestrator.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "vibe.trading_bot.core.orchestrator.start_health_server_task",
        fake_start_health_server_task,
    )

    await orchestrator.run()

    assert events == [
        "cooldown_reset",
        "warmup:True",
        "warmup:False",
    ]