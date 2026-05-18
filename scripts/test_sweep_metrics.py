#!/usr/bin/env python3
"""
Test script to verify SweepResult.to_dict() works correctly.
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.analysis.metrics import BacktestResult, ConvexityMetrics, EquityMetrics
from vibe.backtester.analysis.parameter_sweep import SweepResult
from vibe.common.models.trade import Trade


def test_sweep_result_to_dict():
    """Test that SweepResult.to_dict() uses correct metric attributes."""
    
    # Create mock metrics
    convexity = ConvexityMetrics(
        n_trades=10,
        win_rate=0.6,
        avg_win_r=2.0,
        avg_loss_r=-1.0,
        expectancy_r=0.8,
        max_win_r=5.0,
        max_loss_r=-2.5,
        top10_pct=50.0,
        skewness=0.5,
        max_losing_streak=3,
        total_pnl=5000.0,
        stop_wins=4,
        stop_losses=2,
        eod_wins=2,
        eod_losses=2,
        r_multiples=[2.0, -1.0, 3.0, -0.5],
        first_date="2023-01-01",
        last_date="2023-12-31",
    )
    
    equity = EquityMetrics(
        total_return=0.5,
        annualized_return=0.25,
        sharpe_ratio=1.5,
        max_drawdown=-1000.0,
        max_drawdown_duration_days=30,
        equity_curve=None,
        drawdown_curve=None,
    )
    
    # Create mock trades
    trades = [
        Trade(symbol="QQQ", side="buy", quantity=10, entry_price=100, entry_time=datetime.now(),
              exit_price=110, exit_time=datetime.now(), pnl=100, initial_risk=50, exit_reason="TP"),
        Trade(symbol="QQQ", side="buy", quantity=10, entry_price=100, entry_time=datetime.now(),
              exit_price=95, exit_time=datetime.now(), pnl=-50, initial_risk=50, exit_reason="STOP"),
    ]
    
    backtest_result = BacktestResult(
        overall=convexity,
        by_year={},
        equity=equity,
        trades=trades,
        regime_breakdown={},
        symbol="QQQ",
        start_date="2023-01-01",
        end_date="2023-12-31",
        ruleset_name="test",
        ruleset_version="1.0",
    )
    
    # Create SweepResult
    sweep_result = SweepResult(
        params={"ORB_Duration": 5, "TP_Multiplier": 2.0},
        result=backtest_result,
    )
    
    # Test to_dict
    result_dict = sweep_result.to_dict()
    
    print("✓ SweepResult.to_dict() executed successfully!")
    print(f"\nResult dictionary keys: {list(result_dict.keys())}")
    print(f"\nValues:")
    for key, value in result_dict.items():
        print(f"  {key}: {value}")
    
    # Verify expected keys exist
    expected_keys = [
        "ORB_Duration", "TP_Multiplier",
        "n_trades", "win_rate", "expectancy_r", "total_pnl",
        "max_drawdown", "profit_factor", "avg_win", "avg_loss", "sharpe_ratio"
    ]
    
    for key in expected_keys:
        assert key in result_dict, f"Missing key: {key}"
    
    print(f"\n✓ All {len(expected_keys)} expected keys present!")
    print("\n✓ TEST PASSED!")


if __name__ == "__main__":
    test_sweep_result_to_dict()
