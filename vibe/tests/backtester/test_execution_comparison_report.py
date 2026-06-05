"""Unit tests for compare_execution_modes report (Phase 3 Task 14)."""

from datetime import datetime

import pandas as pd

from vibe.backtester.analysis.metrics import BacktestResult, ConvexityMetrics, EquityMetrics
from vibe.backtester.analysis.performance import compare_execution_modes
from vibe.common.models.trade import Trade


def _convexity(total_pnl: float, n_trades: int) -> ConvexityMetrics:
    return ConvexityMetrics(
        n_trades=n_trades,
        win_rate=0.5,
        avg_win_r=1.0,
        avg_loss_r=-1.0,
        expectancy_r=0.0,
        max_win_r=1.0,
        max_loss_r=-1.0,
        top10_pct=50.0,
        skewness=0.0,
        max_losing_streak=1,
        total_pnl=total_pnl,
        stop_wins=0,
        stop_losses=0,
        eod_wins=0,
        eod_losses=0,
        r_multiples=[],
        first_date="2024-01-01",
        last_date="2024-01-02",
    )


def _equity() -> EquityMetrics:
    idx = pd.date_range("2024-01-01", periods=2, freq="D")
    curve = pd.Series([10_000.0, 10_100.0], index=idx)
    dd = pd.Series([0.0, 0.0], index=idx)
    return EquityMetrics(
        total_return=0.01,
        annualized_return=0.01,
        sharpe_ratio=1.0,
        max_drawdown=0.0,
        max_drawdown_duration_days=0,
        equity_curve=curve,
        drawdown_curve=dd,
    )


def _result(total_pnl: float, trades: list[Trade]) -> BacktestResult:
    return BacktestResult(
        overall=_convexity(total_pnl=total_pnl, n_trades=len(trades)),
        by_year={},
        equity=_equity(),
        trades=trades,
        regime_breakdown={},
        symbol="QQQ",
        start_date="2024-01-01",
        end_date="2024-01-31",
        ruleset_name="test",
        ruleset_version="1.0",
    )


def _trade(entry: float, exit_price: float, qty: float = 10.0, side: str = "buy") -> Trade:
    return Trade(
        symbol="QQQ",
        side=side,
        quantity=qty,
        entry_price=entry,
        exit_price=exit_price,
        entry_time=datetime(2024, 1, 2, 10, 0, 0),
        exit_time=datetime(2024, 1, 2, 11, 0, 0),
        initial_risk=10.0,
        exit_reason="EOD",
    )


def test_comparison_report_with_identical_results_shows_zero_diff():
    trades = [_trade(100.0, 101.0), _trade(102.0, 103.0)]
    legacy = _result(total_pnl=20.0, trades=trades)
    realistic = _result(total_pnl=20.0, trades=trades)

    report = compare_execution_modes(legacy, realistic)

    assert "Trade count diff (realistic - legacy): +0" in report
    assert "Avg entry price diff: +0.000000" in report
    assert "Avg exit price diff: +0.000000" in report
    assert "P&L diff (realistic - legacy): +0.00" in report


def test_comparison_report_with_different_results_shows_diffs():
    legacy_trades = [_trade(100.0, 101.0), _trade(102.0, 103.0)]
    realistic_trades = [_trade(100.2, 100.8), _trade(102.3, 102.7)]

    legacy = _result(total_pnl=20.0, trades=legacy_trades)
    realistic = _result(total_pnl=10.0, trades=realistic_trades)

    report = compare_execution_modes(legacy, realistic)

    assert "Trade count diff (realistic - legacy): +0" in report
    assert "Avg entry price diff: +0.250000" in report
    assert "Avg exit price diff: -0.250000" in report
    assert "P&L diff (realistic - legacy): -10.00" in report
    assert "Slippage Cost Breakdown (Estimated)" in report
