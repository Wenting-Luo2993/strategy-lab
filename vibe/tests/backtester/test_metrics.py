from vibe.backtester.analysis.metrics import ConvexityMetrics, EquityMetrics, BacktestResult


def test_convexity_metrics_fields():
    cm = ConvexityMetrics(
        n_trades=10, win_rate=0.4, avg_win_r=3.0, avg_loss_r=-1.0,
        expectancy_r=0.6, max_win_r=8.0, max_loss_r=-1.5,
        top10_pct=45.0, skewness=1.2, max_losing_streak=3,
        total_pnl=1500.0,
        stop_wins=2, stop_losses=4, eod_wins=2, eod_losses=2,
        r_multiples=[3.0, -1.0, 8.0, -1.0, 2.0, -1.0, 4.0, -1.0, -1.0, 1.5],
        first_date="2024-01-02", last_date="2024-12-31",
    )
    assert cm.n_trades == 10
    assert cm.win_rate == 0.4


def test_backtest_result_fields():
    cm = ConvexityMetrics(
        n_trades=0, win_rate=0.0, avg_win_r=0.0, avg_loss_r=0.0,
        expectancy_r=0.0, max_win_r=0.0, max_loss_r=0.0,
        top10_pct=0.0, skewness=0.0, max_losing_streak=0,
        total_pnl=0.0, stop_wins=0, stop_losses=0, eod_wins=0, eod_losses=0,
        r_multiples=[], first_date="", last_date="",
    )
    import pandas as pd
    eq = EquityMetrics(
        total_return=0.05, annualized_return=0.05, sharpe_ratio=1.0,
        max_drawdown=0.05, max_drawdown_duration_days=10,
        equity_curve=pd.Series(dtype=float),
        drawdown_curve=pd.Series(dtype=float),
    )
    result = BacktestResult(
        overall=cm, by_year={}, equity=eq, trades=[],
        regime_breakdown={}, symbol="QQQ",
        start_date="2024-01-01", end_date="2024-12-31",
        ruleset_name="orb_production", ruleset_version="1.0.0",
    )
    assert result.symbol == "QQQ"
