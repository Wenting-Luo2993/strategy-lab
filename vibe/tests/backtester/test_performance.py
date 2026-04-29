import pytest
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.trade import Trade

ET = ZoneInfo("America/New_York")


def _trade(pnl, initial_risk, exit_reason="EOD", year=2024):
    entry = datetime(year, 6, 1, 10, 0, tzinfo=ET)
    exit_ = datetime(year, 6, 1, 15, 55, tzinfo=ET)
    entry_price = 100.0
    exit_price = entry_price + pnl / 10
    return Trade(
        symbol="QQQ", side="buy", quantity=10,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_time=entry, exit_time=exit_,
        initial_risk=initial_risk,
        exit_reason=exit_reason,
    )


def _equity_curve(n=100, start=10_000.0, end=11_000.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz=ET)
    values = [start + (end - start) * i / (n - 1) for i in range(n)]
    return list(zip(idx.to_pydatetime(), values))


def test_basic_convexity():
    trades = [
        _trade(pnl=300.0, initial_risk=100.0),   # 3R win
        _trade(pnl=-100.0, initial_risk=100.0),   # -1R loss
        _trade(pnl=200.0, initial_risk=100.0),    # 2R win
        _trade(pnl=-100.0, initial_risk=100.0),   # -1R loss
    ]
    result = PerformanceAnalyzer.analyze(
        trades=trades,
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2024, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert result.overall.n_trades == 4
    assert result.overall.win_rate == pytest.approx(0.5)
    assert result.overall.avg_win_r == pytest.approx(2.5)
    assert result.overall.avg_loss_r == pytest.approx(-1.0)
    assert result.overall.expectancy_r == pytest.approx(0.75)


def test_by_year_grouping():
    trades = [
        _trade(pnl=300.0, initial_risk=100.0, year=2023),
        _trade(pnl=-100.0, initial_risk=100.0, year=2024),
    ]
    result = PerformanceAnalyzer.analyze(
        trades=trades,
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2023, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert 2023 in result.by_year
    assert 2024 in result.by_year
    assert result.by_year[2023].n_trades == 1


def test_empty_trades():
    result = PerformanceAnalyzer.analyze(
        trades=[],
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2024, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert result.overall.n_trades == 0
    assert result.overall.win_rate == 0.0


def test_exit_breakdown():
    trades = [
        _trade(pnl=300.0, initial_risk=100.0, exit_reason="STOP"),
        _trade(pnl=-100.0, initial_risk=100.0, exit_reason="STOP"),
        _trade(pnl=200.0, initial_risk=100.0, exit_reason="EOD"),
        _trade(pnl=-50.0, initial_risk=100.0, exit_reason="EOD"),
    ]
    result = PerformanceAnalyzer.analyze(
        trades=trades,
        equity_curve=_equity_curve(),
        initial_capital=10_000.0,
        symbol="QQQ",
        start_date=datetime(2024, 1, 1, tzinfo=ET),
        end_date=datetime(2024, 12, 31, tzinfo=ET),
        ruleset_name="test", ruleset_version="1.0",
    )
    assert result.overall.stop_wins == 1
    assert result.overall.stop_losses == 1
    assert result.overall.eod_wins == 1
    assert result.overall.eod_losses == 1
