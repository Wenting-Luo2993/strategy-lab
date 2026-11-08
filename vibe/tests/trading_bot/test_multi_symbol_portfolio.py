"""Stage 5: five symbols through one trading cycle with shared equity headroom.

All five opening ranges complete in the same cycle, so all five resting OCA
pairs are placed back to back. With a 30% per-position cap and 1.0x gross
leverage the account can fund three full pairs; the fourth is clamped by the
remaining headroom and the fifth is skipped -- and which symbols those are is
decided by ``portfolio.symbol_priority``, not by symbol order.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.common.risk import PositionSizer
from vibe.common.risk.portfolio_sizing import CAPPED_BY_EQUITY_HEADROOM
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.common.strategies import ORBStrategy
from vibe.common.strategies.orb import ORBStrategyConfig
from vibe.trading_bot.config.settings import AppSettings
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.orchestrator import TradingOrchestrator
from vibe.trading_bot.exchange.mock_exchange import MockExchange
from vibe.trading_bot.execution.order_manager import OrderManager
from vibe.trading_bot.execution.portfolio_risk import (
    PortfolioRiskManager,
    SessionEquitySnapshot,
)
from vibe.trading_bot.execution.trade_executor import TradeExecutor

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 7, 20)
SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE"]
PRIORITY = ["EEE", "DDD", "CCC", "BBB", "AAA"]  # deliberately not alphabetical
E0 = 100_000.0


def test_five_symbol_ruleset_is_the_exploration_set():
    ruleset = RuleSetLoader.from_name("orb_resting_stop_paper_5sym")
    assert ruleset.instruments.symbols == ["QQQ", "AMZN", "GOOGL", "MSFT", "TSLA"]
    assert ruleset.portfolio.symbol_priority == ruleset.instruments.symbols
    single = RuleSetLoader.from_name("orb_resting_stop_paper")
    assert ruleset.strategy == single.strategy and ruleset.exit == single.exit


def _ruleset() -> StrategyRuleSet:
    data = RuleSetLoader.from_name("orb_resting_stop_paper_5sym").model_dump()
    data["instruments"]["symbols"] = SYMBOLS
    data["portfolio"]["symbol_priority"] = PRIORITY
    data["portfolio"]["max_symbol_exposure_pct"] = 0.30
    data["portfolio"]["max_open_positions"] = 5
    return StrategyRuleSet.model_validate(data)


def _bars() -> pd.DataFrame:
    """Two completed 5-minute bars: the opening range (99-101) and the next."""
    return pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp(datetime(2026, 7, 20, 9, 30, tzinfo=ET)),
                pd.Timestamp(datetime(2026, 7, 20, 9, 35, tzinfo=ET)),
            ],
            "open": [100.0, 100.2],
            "high": [101.0, 100.6],
            "low": [99.0, 99.6],
            "close": [100.2, 100.1],
            "volume": [50_000.0, 40_000.0],
            "ATR_14": [1.0, 1.0],
        }
    )


@pytest.fixture
def orchestrator(tmp_path):
    ruleset = _ruleset()
    orchestrator = TradingOrchestrator(
        config=AppSettings(
            environment="test",
            database_path=str(tmp_path / "trades.db"),
            broker={"broker_type": "mock", "mode": "paper"},
            trading={"symbols": SYMBOLS},
            dashboard={"enabled": False},
            operational_metrics={"enabled": False},
            notifications={"discord_webhook_url": None},
        ),
        ruleset=ruleset,
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 9, 36), timezone="America/New_York"
        ),
        testing_mode=True,
    )
    exchange = MockExchange(initial_capital=E0, commission_pct=0.0, partial_fill_probability=0.0)
    manager = PortfolioRiskManager(ruleset.portfolio, PositionSizer(risk_pct=0.01))
    manager.record_snapshot(
        SessionEquitySnapshot(SESSION, E0, datetime(2026, 7, 20, 9, 25, tzinfo=ET))
    )
    orchestrator.exchange = exchange
    orchestrator.portfolio_risk_manager = manager
    orchestrator.trade_executor = TradeExecutor(
        exchange=exchange,
        order_manager=OrderManager(exchange=exchange),
        position_sizer=PositionSizer(risk_pct=0.01),
        risk_manager=manager,
    )
    orchestrator.strategy = ORBStrategy(
        config=ORBStrategyConfig(
            name="ORB",
            orb_start_time="09:30",
            orb_duration_minutes=5,
            orb_body_pct_filter=0.0,
            entry_cutoff_time="15:00",
            take_profit_multiplier=0.0,
        )
    )

    async def get_data(symbol, **kwargs):
        return _bars()

    orchestrator.data_manager = SimpleNamespace(get_data=get_data)
    orchestrator.indicator_engine = SimpleNamespace(update=lambda df, **kwargs: df)
    orchestrator.active_provider = None
    for symbol in SYMBOLS:
        # The aggregator's completed 09:30 bar proves the range is complete.
        orchestrator._realtime_bars[symbol] = _bars().iloc[:1].drop(columns=["ATR_14"])
    return orchestrator


@pytest.mark.asyncio
async def test_simultaneous_triggers_are_funded_in_symbol_priority_order(orchestrator, caplog):
    exchange = orchestrator.exchange
    for symbol in SYMBOLS:
        await exchange.set_price(symbol, 100.0)
    placement_order = []
    original = orchestrator.trade_executor.place_resting_stop_entries

    async def recording(symbol, **kwargs):
        placement_order.append(symbol)
        return await original(symbol, **kwargs)

    orchestrator.trade_executor.place_resting_stop_entries = recording
    caplog.set_level(logging.INFO)

    assert await orchestrator._trading_cycle() is True

    assert placement_order == PRIORITY
    entries = [o for o in exchange.get_all_orders().values() if o.order_role == "entry"]
    by_symbol: dict[str, dict[str, float]] = {}
    for order in entries:
        by_symbol.setdefault(order.symbol, {})[order.side] = order.quantity

    # 30% of E0 per position: 297 shares at 101, 303 at 99 (~$30k each side).
    for symbol in ("EEE", "DDD", "CCC"):
        assert by_symbol[symbol] == {"buy": 297, "sell": 303}
    # Three pairs reserve ~$90k; the fourth gets only what is left.
    assert 0 < by_symbol["BBB"]["buy"] < 297 and 0 < by_symbol["BBB"]["sell"] < 303
    contexts = {
        c["symbol"]: c for c in orchestrator.trade_executor._pending_entries.values()
    }
    assert contexts["BBB"]["capped_by"] == CAPPED_BY_EQUITY_HEADROOM
    # Nothing is left for the lowest-priority symbol.
    assert "AAA" not in by_symbol
    reserved = sum(
        max(o.quantity * o.stop_price for o in entries if o.symbol == symbol)
        for symbol in by_symbol
    )
    assert reserved <= E0 + 1e-6

    cycle_logs = [r.getMessage() for r in caplog.records if "[CYCLE]" in r.getMessage()]
    assert cycle_logs and cycle_logs[0].startswith("[CYCLE] 5 symbols")
    await orchestrator.trade_executor.order_manager.shutdown()


def test_cycle_timing_warns_when_the_loop_eats_half_a_bar(orchestrator, caplog):
    caplog.set_level(logging.INFO)
    starts = [("AAA", 0.0), ("BBB", 100.0)]

    timings = orchestrator._log_cycle_timings(starts, 0.0, 200.0)

    assert timings == {"AAA": 100_000.0, "BBB": 100_000.0}
    record = next(r for r in caplog.records if "[CYCLE]" in r.getMessage())
    assert record.levelno == logging.WARNING


def test_cycle_wakes_at_or_end_and_polls_fast_while_resting_entries_are_due(orchestrator):
    orchestrator._base_cycle_interval = 60
    orchestrator._idle_cycle_interval = 300
    orchestrator._testing_mode = False
    orchestrator._has_active_positions = lambda: False

    orchestrator.market_scheduler.set_time(9, 33, 10)
    assert orchestrator._calculate_sleep_interval() == 110 + 2  # wake 2 s after 09:35

    orchestrator.market_scheduler.set_time(9, 36)
    assert orchestrator._calculate_sleep_interval() == orchestrator.RESTING_FAST_POLL_S

    for symbol in SYMBOLS:
        orchestrator._record_entry_attempt(symbol, SESSION)
    assert orchestrator._calculate_sleep_interval() == 300

    orchestrator._entry_attempts.clear()
    orchestrator.market_scheduler.set_time(15, 0)
    assert orchestrator._calculate_sleep_interval() == 300


def test_signal_driven_rulesets_keep_the_base_cadence_while_open(tmp_path):
    orchestrator = TradingOrchestrator(
        config=AppSettings(
            environment="test",
            database_path=str(tmp_path / "trades.db"),
            broker={"broker_type": "mock", "mode": "paper"},
            trading={"symbols": ["QQQ"]},
            dashboard={"enabled": False},
            operational_metrics={"enabled": False},
        ),
        ruleset=RuleSetLoader.from_name("orb_exp073_paper_burn_in"),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 10, 0), timezone="America/New_York"
        ),
        testing_mode=False,
    )
    # IBKR-like engine: no synchronous position list.
    orchestrator.exchange = SimpleNamespace()
    orchestrator.trade_executor = None

    assert orchestrator._has_active_positions() is False
    assert orchestrator._calculate_sleep_interval() == 60
    orchestrator.trade_store.close()
