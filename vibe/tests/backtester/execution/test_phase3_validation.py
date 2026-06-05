"""Phase 3 validation tests (Tasks 11-12)."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.backtester.core.execution.models import Order
from vibe.backtester.core.execution.simulator import ExecutionSimulator
from vibe.backtester.core.fill_simulator import FillSimulator
from vibe.common.models.bar import Bar
from vibe.common.ruleset.loader import RuleSetLoader


ET = ZoneInfo("America/New_York")
PARQUET_DIR = Path("vibe/data/parquet")

pytestmark = pytest.mark.skipif(
    not (PARQUET_DIR / "QQQ.parquet").exists(),
    reason="Parquet data not available",
)


def _result_signature(result):
    """Deterministic signature from stable backtest outputs."""
    trade_sig = tuple(
        (
            t.side,
            t.quantity,
            round(t.entry_price, 8),
            round(t.exit_price or 0.0, 8),
            t.entry_time.isoformat(),
            (t.exit_time.isoformat() if t.exit_time else ""),
            round(t.pnl or 0.0, 8),
            t.exit_reason or "",
        )
        for t in result.trades
    )
    return (
        result.overall.n_trades,
        round(result.overall.total_pnl, 8),
        round(result.overall.expectancy_r, 8),
        round(result.overall.win_rate, 8),
        trade_sig,
    )


def _run_backtest(execution_config: ExecutionConfig | None):
    ruleset = RuleSetLoader.from_name("orb_production")
    engine = BacktestEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
        slippage_ticks=5,
        execution_config=execution_config,
    )
    return engine.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 31, tzinfo=ET),
    )


def test_determinism_legacy_config_3_runs():
    signatures = []
    for _ in range(3):
        result = _run_backtest(ExecutionConfig.legacy(slippage_ticks=5))
        signatures.append(_result_signature(result))

    assert signatures[0] == signatures[1] == signatures[2]


def test_determinism_realistic_config_3_runs():
    realistic = ExecutionConfig.realistic(
        slippage_k=0.35,
        participation_rate=0.03,
        impact_k=0.35,
    )

    signatures = []
    for _ in range(3):
        result = _run_backtest(realistic)
        signatures.append(_result_signature(result))

    assert signatures[0] == signatures[1] == signatures[2]


def test_legacy_config_matches_old_fill_simulator():
    bar = Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000_000,
    )

    old = FillSimulator(slippage_ticks=5)
    old_fill = old.execute("QQQ", "buy", quantity=100, bar=bar)

    execution = ExecutionSimulator(ExecutionConfig.legacy(slippage_ticks=5))
    order = Order(
        id="o1",
        symbol="QQQ",
        side="buy",
        size=100,
        order_type="market",
        limit_price=None,
        timestamp=bar.timestamp,
        signal_bar_index=0,
    )
    new_fill = execution.execute_market_order(order, bar)

    assert old_fill.filled_qty == pytest.approx(new_fill.qty)
    assert old_fill.avg_price == pytest.approx(new_fill.price)


def test_realistic_config_degrades_vs_legacy():
    bar = Bar(
        timestamp=datetime(2024, 1, 15, 10, 0, tzinfo=ET),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=100_000,
    )
    order = Order(
        id="deg_1",
        symbol="QQQ",
        side="buy",
        size=50_000,
        order_type="market",
        limit_price=None,
        timestamp=bar.timestamp,
        signal_bar_index=0,
        price_override=None,
    )

    legacy_fill = ExecutionSimulator(
        ExecutionConfig.legacy(slippage_ticks=5)
    ).execute_market_order(order, bar, adv=2_000_000)
    realistic_fill = ExecutionSimulator(
        ExecutionConfig.realistic(
            slippage_k=0.5,
            participation_rate=0.02,
            impact_k=0.5,
        )
    ).execute_market_order(order, bar, adv=2_000_000)

    # Realistic mode should be less favorable for a buy: higher price and smaller/equal quantity.
    assert realistic_fill.price > legacy_fill.price
    assert realistic_fill.qty <= legacy_fill.qty


def test_engine_default_no_config_matches_explicit_legacy():
    ruleset = RuleSetLoader.from_name("orb_production")

    engine_default = BacktestEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
        slippage_ticks=5,
        execution_config=None,
    )
    engine_legacy = BacktestEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
        slippage_ticks=5,
        execution_config=ExecutionConfig.legacy(slippage_ticks=5),
    )

    result_default = engine_default.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 31, tzinfo=ET),
    )
    result_legacy = engine_legacy.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 31, tzinfo=ET),
    )

    assert _result_signature(result_default) == _result_signature(result_legacy)


def test_engine_realistic_opt_in_uses_model_pricing_path(monkeypatch):
    ruleset = RuleSetLoader.from_name("orb_production")
    observed_price_overrides: list[float | None] = []

    original_execute_order = ExecutionSimulator.execute_order

    def _spy_execute_order(self, order, bar, adv=None):
        observed_price_overrides.append(order.price_override)
        return original_execute_order(self, order, bar, adv)

    monkeypatch.setattr(ExecutionSimulator, "execute_order", _spy_execute_order)

    engine_realistic = BacktestEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
        slippage_ticks=5,
        execution_config=ExecutionConfig.realistic(),
    )

    engine_realistic.run(
        symbol="QQQ",
        start_date=datetime(2024, 1, 2, tzinfo=ET),
        end_date=datetime(2024, 1, 10, tzinfo=ET),
    )

    assert observed_price_overrides, "Expected at least one executed order in realistic mode"
    assert all(value is None for value in observed_price_overrides)
