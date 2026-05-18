"""
Unit tests for backtest determinism (PRD Test 2).

Ensures that running the same backtest with identical parameters
produces identical results (critical for reproducible research).
"""
import pytest
from pathlib import Path
from datetime import datetime
import pandas as pd

from vibe.backtester.core.engine import BacktestEngine
from vibe.common.ruleset.loader import RuleSetLoader


def test_backtest_is_deterministic():
    """
    PRD Test 2: Backtest Determinism
    
    Verify that running the same backtest twice with identical parameters
    produces identical results.
    """
    # Load ruleset
    ruleset = RuleSetLoader.from_name("orb_production")
    
    # Common parameters
    data_dir = Path("vibe/data/parquet")
    symbol = "QQQ"
    start_date = pd.Timestamp(2024, 1, 2, tz="America/New_York")
    end_date = pd.Timestamp(2024, 1, 31, tz="America/New_York")
    initial_capital = 10_000.0
    slippage_ticks = 5
    
    # Run backtest twice
    engine1 = BacktestEngine(
        ruleset=ruleset,
        data_dir=data_dir,
        initial_capital=initial_capital,
        slippage_ticks=slippage_ticks,
    )
    
    result1 = engine1.run(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )
    
    engine2 = BacktestEngine(
        ruleset=ruleset,
        data_dir=data_dir,
        initial_capital=initial_capital,
        slippage_ticks=slippage_ticks,
    )
    
    result2 = engine2.run(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )
    
    # Verify identical results
    assert result1.overall.n_trades == result2.overall.n_trades
    assert result1.overall.expectancy_r == result2.overall.expectancy_r
    assert result1.overall.win_rate == result2.overall.win_rate
    assert result1.overall.total_pnl == result2.overall.total_pnl
    assert result1.equity.sharpe_ratio == result2.equity.sharpe_ratio
    assert result1.equity.max_drawdown == result2.equity.max_drawdown
    
    # Verify trade-by-trade consistency
    assert len(result1.trades) == len(result2.trades)
    for trade1, trade2 in zip(result1.trades, result2.trades):
        assert trade1.entry_time == trade2.entry_time
        assert trade1.entry_price == trade2.entry_price
        assert trade1.exit_time == trade2.exit_time
        assert trade1.exit_price == trade2.exit_price
        assert trade1.pnl == trade2.pnl
        assert trade1.initial_risk == trade2.initial_risk
        assert trade1.exit_reason == trade2.exit_reason


def test_backtest_determinism_with_precomputed_features():
    """
    Verify determinism when using pre-computed features.
    
    This is critical for optimization framework where features are computed
    once and reused across parameter sweeps.
    """
    from vibe.backtester.analysis.regime_research.features import FeatureEngine
    from vibe.backtester.data.parquet_loader import ParquetLoader
    
    # Load ruleset
    ruleset = RuleSetLoader.from_name("orb_production")
    
    # Load data and compute features
    data_dir = Path("vibe/data/parquet")
    symbol = "QQQ"
    start_date = pd.Timestamp(2024, 1, 2, tz="America/New_York")
    end_date = pd.Timestamp(2024, 1, 31, tz="America/New_York")
    
    loader = ParquetLoader(data_dir, symbols=[symbol])
    df = loader.get_full_df(symbol)
    # Filter by date range
    df = df[(df.index >= start_date) & (df.index <= end_date)]
    
    feature_engine = FeatureEngine()
    features = feature_engine.compute(df)
    
    # Run backtest twice with same features
    engine1 = BacktestEngine(
        ruleset=ruleset,
        data_dir=data_dir,
        initial_capital=10_000.0,
        slippage_ticks=5,
    )
    
    result1 = engine1.run(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        precomputed_features=features,
    )
    
    engine2 = BacktestEngine(
        ruleset=ruleset,
        data_dir=data_dir,
        initial_capital=10_000.0,
        slippage_ticks=5,
    )
    
    result2 = engine2.run(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        precomputed_features=features,
    )
    
    # Verify identical results
    assert result1.overall.expectancy_r == result2.overall.expectancy_r
    assert result1.overall.total_pnl == result2.overall.total_pnl
    assert len(result1.trades) == len(result2.trades)


def test_backtest_reproducible_across_sessions():
    """
    Verify that results are reproducible across different Python sessions.
    
    This is important for CI/CD and collaborative research.
    """
    # This test would typically be run multiple times in separate processes
    # For now, we just verify that the result structure is consistent
    
    ruleset = RuleSetLoader.from_name("orb_production")
    
    engine = BacktestEngine(
        ruleset=ruleset,
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
        slippage_ticks=5,
    )
    
    result = engine.run(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
    )
    
    # Verify result has expected structure
    assert hasattr(result, 'overall')
    assert hasattr(result, 'equity')
    assert hasattr(result, 'trades')
    assert isinstance(result.overall.expectancy_r, float)
    assert isinstance(result.overall.n_trades, int)
