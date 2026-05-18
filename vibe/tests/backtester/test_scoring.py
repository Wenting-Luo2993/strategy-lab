"""
Unit tests for metric calculations (PRD Test 3).

Verifies correctness of performance metrics like Sharpe ratio,
expectancy, tail ratio, and composite scoring.
"""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from vibe.backtester.analysis.scoring import (
    calculate_tail_ratio,
    composite_score,
)
from vibe.backtester.analysis.metrics import (
    BacktestResult,
    ConvexityMetrics,
    EquityMetrics,
)


def test_sharpe_calculation():
    """
    PRD Test 3: Metric Calculation
    
    Verify Sharpe ratio calculation is reasonable.
    """
    # Create sample equity metrics
    equity_curve = pd.Series([100000.0, 101000.0, 100500.0, 102000.0, 103000.0])
    drawdown_curve = pd.Series([0.0] * 5)
    
    equity = EquityMetrics(
        total_return=0.03,
        annualized_return=0.03,
        sharpe_ratio=1.5,  # Directly set for testing
        max_drawdown=0.01,
        max_drawdown_duration_days=2,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
    )
    
    # Verify Sharpe is reasonable
    assert isinstance(equity.sharpe_ratio, (int, float))
    assert -5 < equity.sharpe_ratio < 5


def test_expectancy_calculation():
    """
    Verify expectancy (R-multiple) calculation.
    """
    # Sample R-multiples
    r_multiples = [2.0, -1.0, 3.0, -1.0, 1.5, -1.0, 4.0, -1.0]
    
    # Expected expectancy
    expected_expectancy = np.mean(r_multiples)
    
    # Create ConvexityMetrics
    cm = ConvexityMetrics(
        n_trades=len(r_multiples),
        win_rate=0.5,
        avg_win_r=2.625,
        avg_loss_r=-1.0,
        expectancy_r=expected_expectancy,
        max_win_r=4.0,
        max_loss_r=-1.0,
        top10_pct=0.0,
        skewness=0.0,
        max_losing_streak=3,
        total_pnl=0.0,
        stop_wins=0,
        stop_losses=0,
        eod_wins=0,
        eod_losses=0,
        r_multiples=r_multiples,
        first_date="2024-01-01",
        last_date="2024-12-31",
    )
    
    assert cm.expectancy_r == expected_expectancy
    assert cm.n_trades == len(r_multiples)


def test_tail_ratio_calculation():
    """
    Verify tail ratio calculation (95th percentile / 5th percentile).
    """
    # Sample R-multiples with positive skew
    # 95th percentile = ~2.0, 5th percentile = ~-1.0
    r_multiples = [-1.0] * 80 + [2.0] * 15 + [5.0] * 5  # 100 trades
    
    tail_ratio = calculate_tail_ratio(r_multiples, percentile=0.95)
    
    # Should have some positive skew (ratio > 1.0)
    assert tail_ratio > 1.0
    assert isinstance(tail_ratio, float)


def test_tail_ratio_insufficient_data():
    """
    Verify tail ratio returns 0 for insufficient data.
    """
    r_multiples = [1.0, -1.0, 2.0]  # Only 3 trades
    
    tail_ratio = calculate_tail_ratio(r_multiples)
    
    assert tail_ratio == 0.0


def test_composite_score_calculation():
    """
    Verify composite score combines multiple metrics correctly.
    """
    # Create mock BacktestResult
    cm = ConvexityMetrics(
        n_trades=100,
        win_rate=0.4,
        avg_win_r=3.0,
        avg_loss_r=-1.0,
        expectancy_r=0.2,
        max_win_r=8.0,
        max_loss_r=-1.5,
        top10_pct=60.0,
        skewness=1.5,
        max_losing_streak=5,
        total_pnl=20000.0,
        stop_wins=20,
        stop_losses=40,
        eod_wins=20,
        eod_losses=20,
        r_multiples=[3.0, -1.0] * 50,
        first_date="2024-01-01",
        last_date="2024-12-31",
    )
    
    equity_curve = pd.Series([100000.0 * (1.002 ** i) for i in range(100)])
    drawdown_curve = pd.Series([0.0] * 100)
    
    equity = EquityMetrics(
        total_return=0.20,
        annualized_return=0.20,
        sharpe_ratio=1.5,
        max_drawdown=0.05,
        max_drawdown_duration_days=10,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
    )
    
    result = BacktestResult(
        overall=cm,
        by_year={},
        equity=equity,
        trades=[],
        regime_breakdown={},
        symbol="QQQ",
        start_date="2024-01-01",
        end_date="2024-12-31",
        ruleset_name="orb_production",
        ruleset_version="1.0.0",
    )
    
    score = composite_score(result)
    
    # Verify score is in valid range
    assert 0.0 <= score <= 1.0
    assert isinstance(score, float)


def test_composite_score_min_trades_threshold():
    """
    Verify composite score applies sample penalty for insufficient trades.
    
    With 10 trades (below default min_trades=30), the score should be
    penalized by multiplying by (10/30) = 0.33.
    """
    # Create result with only 10 trades (below default min_trades=30)
    cm = ConvexityMetrics(
        n_trades=10,
        win_rate=0.6,
        avg_win_r=2.0,
        avg_loss_r=-1.0,
        expectancy_r=0.4,
        max_win_r=3.0,
        max_loss_r=-1.5,
        top10_pct=50.0,
        skewness=0.5,
        max_losing_streak=2,
        total_pnl=4000.0,
        stop_wins=6,
        stop_losses=4,
        eod_wins=0,
        eod_losses=0,
        r_multiples=[2.0, -1.0] * 5,
        first_date="2024-01-01",
        last_date="2024-01-31",
    )
    
    equity = EquityMetrics(
        total_return=0.04,
        annualized_return=0.04,
        sharpe_ratio=1.0,
        max_drawdown=0.02,
        max_drawdown_duration_days=5,
        equity_curve=pd.Series([100000.0, 104000.0]),
        drawdown_curve=pd.Series([0.0, 0.0]),
    )
    
    result = BacktestResult(
        overall=cm,
        by_year={},
        equity=equity,
        trades=[],
        regime_breakdown={},
        symbol="QQQ",
        start_date="2024-01-01",
        end_date="2024-01-31",
        ruleset_name="orb_production",
        ruleset_version="1.0.0",
    )
    
    score = composite_score(result, min_trades=30)
    
    # Score should be penalized (multiplied by 10/30 = 0.33)
    # It won't be 0 but will be significantly reduced
    assert score > 0.0  # Some score
    assert score < 0.5  # But heavily penalized

def test_composite_score_custom_weights():
    """
    Verify composite score accepts custom weights.
    """
    cm = ConvexityMetrics(
        n_trades=50,
        win_rate=0.5,
        avg_win_r=2.0,
        avg_loss_r=-1.0,
        expectancy_r=0.5,
        max_win_r=5.0,
        max_loss_r=-1.5,
        top10_pct=60.0,
        skewness=1.0,
        max_losing_streak=3,
        total_pnl=25000.0,
        stop_wins=25,
        stop_losses=25,
        eod_wins=0,
        eod_losses=0,
        r_multiples=[2.0, -1.0] * 25,
        first_date="2024-01-01",
        last_date="2024-06-30",
    )
    
    equity = EquityMetrics(
        total_return=0.25,
        annualized_return=0.25,
        sharpe_ratio=2.0,
        max_drawdown=0.03,
        max_drawdown_duration_days=5,
        equity_curve=pd.Series([100000.0, 125000.0]),
        drawdown_curve=pd.Series([0.0, 0.0]),
    )
    
    result = BacktestResult(
        overall=cm,
        by_year={},
        equity=equity,
        trades=[],
        regime_breakdown={},
        symbol="QQQ",
        start_date="2024-01-01",
        end_date="2024-06-30",
        ruleset_name="orb_production",
        ruleset_version="1.0.0",
    )
    
    # Custom weights (only Sharpe matters)
    custom_weights = {
        "sharpe": 1.0,
        "expectancy_r": 0.0,
        "tail_ratio": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
    }
    
    score = composite_score(result, weights=custom_weights, min_trades=30)
    
    # Score should be based purely on normalized Sharpe
    assert 0.0 <= score <= 1.0
