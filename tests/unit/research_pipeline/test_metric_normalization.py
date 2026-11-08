"""P1 - normalized metric definitions.

Each test pins one definition that section 7 of the backtest pipeline plan
identified as inconsistent between modules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from vibe.backtester.analysis.metrics import METRIC_CALCULATION_VERSION
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.common.models.trade import Trade

BASE = datetime(2022, 3, 1, 14, 30, tzinfo=timezone.utc)


def _trade(r: float, *, risk: float = 100.0, day: int = 0,
           exit_reason: str = "STOP") -> Trade:
    """Build a trade whose R-multiple is exactly ``r``.

    P&L is derived from prices by the model, so the prices are chosen to
    produce the intended P&L rather than the P&L being supplied.
    """
    qty = 10.0
    entry = 100.0
    pnl = r * risk
    return Trade(
        symbol="QQQ", side="buy", quantity=qty,
        entry_price=entry, exit_price=entry + pnl / qty,
        entry_time=BASE + timedelta(days=day),
        exit_time=BASE + timedelta(days=day, hours=5),
        initial_risk=risk, exit_reason=exit_reason,
    )


class TestWinLossBreakevenPartition:
    def test_zero_r_is_breakeven_not_a_loss(self):
        m = PerformanceAnalyzer._calc_convexity(
            [_trade(1.0), _trade(-1.0), _trade(0.0)]
        )
        assert (m.winning_trades, m.losing_trades, m.breakeven_trades) == (1, 1, 1)

    def test_counts_partition_the_r_sample(self):
        trades = [_trade(r) for r in (2.0, -1.0, 0.0, 0.5, -0.25, 0.0)]
        m = PerformanceAnalyzer._calc_convexity(trades)
        assert (
            m.winning_trades + m.losing_trades + m.breakeven_trades
            == m.r_sample_size
        )

    def test_avg_loss_excludes_breakeven(self):
        """Folding zeros into losses drags avg_loss toward zero."""
        m = PerformanceAnalyzer._calc_convexity(
            [_trade(-1.0), _trade(0.0), _trade(0.0)]
        )
        assert m.avg_loss_r == pytest.approx(-1.0)

    def test_win_rate_counts_only_strict_wins(self):
        m = PerformanceAnalyzer._calc_convexity(
            [_trade(1.0), _trade(0.0), _trade(-1.0), _trade(-1.0)]
        )
        assert m.win_rate == pytest.approx(0.25)

    def test_breakeven_breaks_a_losing_streak(self):
        trades = [_trade(-1.0), _trade(-1.0), _trade(0.0), _trade(-1.0)]
        m = PerformanceAnalyzer._calc_convexity(trades)
        assert m.max_losing_streak == 2

    def test_stop_and_eod_splits_exclude_breakeven(self):
        m = PerformanceAnalyzer._calc_convexity(
            [_trade(0.0, exit_reason="STOP"), _trade(0.0, exit_reason="EOD")]
        )
        assert (m.stop_wins, m.stop_losses, m.eod_wins, m.eod_losses) == (0, 0, 0, 0)


class TestExpectancy:
    def test_equals_the_mean_r(self):
        trades = [_trade(r) for r in (2.0, -1.0, 0.0, 0.5)]
        m = PerformanceAnalyzer._calc_convexity(trades)
        assert m.expectancy_r == pytest.approx(np.mean([2.0, -1.0, 0.0, 0.5]))

    def test_unchanged_by_the_three_way_split(self):
        """The old wins/losses form equalled the mean; the new one still must."""
        rs = [1.5, -1.0, 0.0, 0.0, 3.0, -1.0]
        m = PerformanceAnalyzer._calc_convexity([_trade(r) for r in rs])
        assert m.expectancy_r == pytest.approx(sum(rs) / len(rs))


class TestDroppedTradeAccounting:
    def test_n_trades_counts_every_trade(self):
        good = _trade(1.0)
        bad = _trade(1.0)
        bad.initial_risk = 0.0
        m = PerformanceAnalyzer._calc_convexity([good, bad])
        assert m.n_trades == 2
        assert m.r_sample_size == 1
        assert m.dropped_trade_count == 1

    def test_total_pnl_includes_dropped_trades(self):
        """P&L must reconcile against the equity curve, not the R sample."""
        good = _trade(1.0, risk=100.0)
        bad = _trade(2.0, risk=100.0)
        bad.initial_risk = None
        m = PerformanceAnalyzer._calc_convexity([good, bad])
        assert m.total_pnl == pytest.approx(good.pnl + bad.pnl)

    def test_all_dropped_still_reports_pnl_and_census(self):
        bad = _trade(1.0)
        bad.initial_risk = -5.0
        m = PerformanceAnalyzer._calc_convexity([bad])
        assert m.n_trades == 1
        assert m.r_sample_size == 0
        assert m.dropped_trade_count == 1
        assert m.total_pnl == pytest.approx(bad.pnl)

    def test_no_trades(self):
        m = PerformanceAnalyzer._calc_convexity([])
        assert (m.n_trades, m.r_sample_size, m.dropped_trade_count) == (0, 0, 0)
        assert m.total_pnl == 0.0


def _curve(values, start=BASE, step=timedelta(minutes=5)):
    return [(start + i * step, float(v)) for i, v in enumerate(values)]


class TestEquityMetrics:
    def test_sharpe_uses_session_returns(self):
        """A per-bar annualization inflates Sharpe by roughly sqrt(bars/day)."""
        rng = np.random.default_rng(0)
        values, level = [], 100_000.0
        start = datetime(2022, 1, 3, 14, 30, tzinfo=timezone.utc)
        points = []
        for day in range(60):
            for bar in range(78):
                level *= 1 + rng.normal(0.00002, 0.0004)
                points.append(
                    (start + timedelta(days=day, minutes=5 * bar), level)
                )
        m = PerformanceAnalyzer._calc_equity(points, 100_000.0)

        eq = pd.Series([v for _, v in points],
                       index=pd.DatetimeIndex([t for t, _ in points]))
        sessions = eq.resample("1D").last().dropna().pct_change().dropna()
        expected = float(sessions.mean() / sessions.std() * np.sqrt(252))
        assert m.sharpe_ratio == pytest.approx(expected, rel=1e-9)

    def test_bars_per_session_is_measured_not_assumed(self):
        start = datetime(2022, 1, 3, 14, 30, tzinfo=timezone.utc)
        points = [
            (start + timedelta(days=d, minutes=5 * b), 100_000.0 + b)
            for d in range(3) for b in range(12)
        ]
        m = PerformanceAnalyzer._calc_equity(points, 100_000.0)
        assert m.bars_per_session == 12
        assert m.n_sessions == 3

    def test_max_drawdown_is_a_negative_fraction(self):
        m = PerformanceAnalyzer._calc_equity(
            _curve([100.0, 120.0, 60.0, 90.0]), 100.0
        )
        assert m.max_drawdown == pytest.approx(-0.5)
        assert -1.0 <= m.max_drawdown <= 0.0

    def test_drawdown_duration_is_calendar_days(self):
        start = datetime(2022, 1, 3, tzinfo=timezone.utc)
        points = [
            (start, 100.0),
            (start + timedelta(days=1), 90.0),
            (start + timedelta(days=5), 95.0),
            (start + timedelta(days=9), 101.0),
        ]
        m = PerformanceAnalyzer._calc_equity(points, 100.0)
        assert m.max_drawdown_duration_days == 8

    def test_unrecovered_drawdown_measures_to_the_end(self):
        start = datetime(2022, 1, 3, tzinfo=timezone.utc)
        points = [
            (start, 100.0),
            (start + timedelta(days=2), 80.0),
            (start + timedelta(days=10), 85.0),
        ]
        m = PerformanceAnalyzer._calc_equity(points, 100.0)
        assert m.max_drawdown_duration_days == 8

    def test_flat_curve_has_no_drawdown(self):
        m = PerformanceAnalyzer._calc_equity(_curve([100.0] * 10), 100.0)
        assert m.max_drawdown == pytest.approx(0.0)
        assert m.max_drawdown_duration_days == 0

    def test_empty_curve(self):
        m = PerformanceAnalyzer._calc_equity([], 100.0)
        assert m.sharpe_ratio == 0.0
        assert m.n_sessions == 0

    def test_single_session_cannot_produce_a_sharpe(self):
        """One session gives no return sample; report 0 rather than NaN."""
        m = PerformanceAnalyzer._calc_equity(
            _curve([100.0, 101.0, 102.0]), 100.0
        )
        assert m.sharpe_ratio == 0.0
        assert not np.isnan(m.sharpe_ratio)


class TestCalculationVersion:
    def test_convexity_carries_a_version(self):
        m = PerformanceAnalyzer._calc_convexity([_trade(1.0)])
        assert m.calculation_version == METRIC_CALCULATION_VERSION

    def test_equity_carries_a_version(self):
        m = PerformanceAnalyzer._calc_equity(_curve([100.0, 101.0]), 100.0)
        assert m.calculation_version == METRIC_CALCULATION_VERSION

    def test_version_is_at_least_two(self):
        """Version 1 was the pre-normalization definition set."""
        assert METRIC_CALCULATION_VERSION >= 2
