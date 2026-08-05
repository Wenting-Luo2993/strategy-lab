"""Tests for trading bot warmup phase behavior."""

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