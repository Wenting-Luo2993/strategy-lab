"""Compressed lifecycle simulation tests for the trading bot."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator


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