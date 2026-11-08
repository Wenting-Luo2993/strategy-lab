from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)
from vibe.common.models.trade import Trade


def compare_execution_modes(
    legacy_result: BacktestResult,
    realistic_result: BacktestResult,
) -> str:
    """
    Build a markdown A/B report comparing legacy vs realistic execution modes.

    The report focuses on practical differences for review:
    - trade count and fill-level price averages
    - total P&L and win rate deltas
    - estimated execution-cost drift from entry/exit price differences
    """
    legacy_trades = legacy_result.trades
    realistic_trades = realistic_result.trades

    legacy_trade_count = len(legacy_trades)
    realistic_trade_count = len(realistic_trades)
    trade_count_diff = realistic_trade_count - legacy_trade_count

    def _avg_entry(trades: List[Trade]) -> float:
        if not trades:
            return 0.0
        return float(sum(t.entry_price for t in trades) / len(trades))

    def _avg_exit(trades: List[Trade]) -> float:
        exits = [t.exit_price for t in trades if t.exit_price is not None]
        if not exits:
            return 0.0
        return float(sum(exits) / len(exits))

    legacy_avg_entry = _avg_entry(legacy_trades)
    realistic_avg_entry = _avg_entry(realistic_trades)
    avg_entry_diff = realistic_avg_entry - legacy_avg_entry

    legacy_avg_exit = _avg_exit(legacy_trades)
    realistic_avg_exit = _avg_exit(realistic_trades)
    avg_exit_diff = realistic_avg_exit - legacy_avg_exit

    legacy_pnl = legacy_result.overall.total_pnl
    realistic_pnl = realistic_result.overall.total_pnl
    pnl_diff = realistic_pnl - legacy_pnl

    legacy_win_rate = legacy_result.overall.win_rate
    realistic_win_rate = realistic_result.overall.win_rate
    win_rate_diff = realistic_win_rate - legacy_win_rate

    paired_count = min(legacy_trade_count, realistic_trade_count)
    entry_execution_cost = 0.0
    exit_execution_cost = 0.0
    for idx in range(paired_count):
        legacy_trade = legacy_trades[idx]
        realistic_trade = realistic_trades[idx]
        side_mult = 1.0 if legacy_trade.side == "buy" else -1.0
        qty = min(legacy_trade.quantity, realistic_trade.quantity)

        entry_execution_cost += (
            (realistic_trade.entry_price - legacy_trade.entry_price) * side_mult * qty
        )

        if legacy_trade.exit_price is not None and realistic_trade.exit_price is not None:
            # Exit is adverse in opposite direction to entry.
            exit_execution_cost += (
                (legacy_trade.exit_price - realistic_trade.exit_price) * side_mult * qty
            )

    total_estimated_execution_cost = entry_execution_cost + exit_execution_cost

    lines = [
        "## Execution Mode Comparison",
        "",
        "### Summary",
        f"- Legacy trades: {legacy_trade_count}",
        f"- Realistic trades: {realistic_trade_count}",
        f"- Trade count diff (realistic - legacy): {trade_count_diff:+d}",
        "",
        "### Fill Price Deltas",
        f"- Avg entry price diff: {avg_entry_diff:+.6f}",
        f"- Avg exit price diff: {avg_exit_diff:+.6f}",
        "",
        "### Performance Deltas",
        f"- Total P&L (legacy): {legacy_pnl:+.2f}",
        f"- Total P&L (realistic): {realistic_pnl:+.2f}",
        f"- P&L diff (realistic - legacy): {pnl_diff:+.2f}",
        f"- Win rate diff (realistic - legacy): {win_rate_diff:+.4f}",
        "",
        "### Slippage Cost Breakdown (Estimated)",
        f"- Entry execution cost delta: {entry_execution_cost:+.2f}",
        f"- Exit execution cost delta: {exit_execution_cost:+.2f}",
        f"- Total estimated execution cost delta: {total_estimated_execution_cost:+.2f}",
        "",
        f"Paired-trade sample size used for execution-cost estimate: {paired_count}",
    ]
    return "\n".join(lines)


class PerformanceAnalyzer:

    @staticmethod
    def analyze(
        trades: List[Trade],
        equity_curve: List[Tuple[datetime, float]],
        initial_capital: float,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        ruleset_name: str,
        ruleset_version: str,
        execution_diagnostics: Optional[Dict[str, float]] = None,
    ) -> BacktestResult:
        overall = PerformanceAnalyzer._calc_convexity(trades)
        by_year = PerformanceAnalyzer._calc_by_year(trades)
        equity  = PerformanceAnalyzer._calc_equity(equity_curve, initial_capital)
        return BacktestResult(
            overall=overall,
            by_year=by_year,
            equity=equity,
            trades=trades,
            regime_breakdown={},
            symbol=symbol,
            start_date=start_date.date().isoformat(),
            end_date=end_date.date().isoformat(),
            ruleset_name=ruleset_name,
            ruleset_version=ruleset_version,
            execution_diagnostics=dict(execution_diagnostics or {}),
        )

    @staticmethod
    def _net_pnl(trade: Trade) -> float:
        """P&L after costs.

        ``Trade.pnl`` is derived from prices alone and is therefore gross.
        Commission is carried separately, so net P&L is the difference. Every
        R-multiple and cash total below uses this.
        """
        return trade.pnl - (trade.commission or 0.0)

    @staticmethod
    def _calc_convexity(trades: List[Trade]) -> ConvexityMetrics:
        # Every trade counts for cash and census purposes. Only trades with a
        # usable risk denominator can carry an R-multiple, and the gap between
        # the two populations is reported rather than hidden.
        valid = [t for t in trades if t.initial_risk and t.initial_risk > 0]
        dropped = len(trades) - len(valid)
        net = PerformanceAnalyzer._net_pnl
        gross_pnl = sum(t.pnl for t in trades)
        total_costs = sum(t.commission or 0.0 for t in trades)
        total_pnl = gross_pnl - total_costs

        if not valid:
            return ConvexityMetrics(
                n_trades=len(trades), win_rate=0.0, avg_win_r=0.0, avg_loss_r=0.0,
                expectancy_r=0.0, max_win_r=0.0, max_loss_r=0.0,
                top10_pct=0.0, skewness=0.0, max_losing_streak=0,
                total_pnl=total_pnl, stop_wins=0, stop_losses=0,
                eod_wins=0, eod_losses=0, r_multiples=[],
                first_date="", last_date="",
                winning_trades=0, losing_trades=0, breakeven_trades=0,
                r_sample_size=0, dropped_trade_count=dropped,
                gross_pnl=gross_pnl, total_costs=total_costs,
            )

        r_list = [net(t) / t.initial_risk for t in valid]
        wins      = [r for r in r_list if r > 0]
        losses    = [r for r in r_list if r < 0]
        breakeven = [r for r in r_list if r == 0]
        wr = len(wins) / len(r_list)
        avg_win  = float(np.mean(wins))   if wins   else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        gross_profit = sum(net(t) for t in valid if net(t) > 0)
        top_n = max(1, len(valid) // 10)
        top_pnls = sorted([net(t) for t in valid], reverse=True)[:top_n]
        top10_pct = (sum(top_pnls) / gross_profit * 100) if gross_profit > 0 else 0.0

        mean_r = float(np.mean(r_list))
        std_r  = float(np.std(r_list))
        skew = (float(np.mean([(r - mean_r) ** 3 for r in r_list])) / std_r ** 3
                if std_r > 0 else 0.0)

        # Mean of the R sample directly. The previous
        # ``wr * avg_win + (1 - wr) * avg_loss`` form was algebraically the
        # same only while wins and losses partitioned the sample; with
        # breakeven split out it would no longer be, and the mean is the
        # definition anyway.
        expectancy = mean_r

        # A breakeven trade ends a losing streak rather than extending it,
        # consistent with it not being a loss.
        streak = cur = 0
        for r in r_list:
            cur = cur + 1 if r < 0 else 0
            streak = max(streak, cur)

        stop_trades = [t for t in valid if t.exit_reason == "STOP"]
        eod_trades  = [t for t in valid if t.exit_reason == "EOD"]

        return ConvexityMetrics(
            n_trades=len(trades),
            win_rate=wr,
            avg_win_r=avg_win,
            avg_loss_r=avg_loss,
            expectancy_r=expectancy,
            max_win_r=max(r_list),
            max_loss_r=min(r_list),
            top10_pct=top10_pct,
            skewness=skew,
            max_losing_streak=streak,
            total_pnl=total_pnl,
            stop_wins=sum(1 for t in stop_trades if net(t) > 0),
            stop_losses=sum(1 for t in stop_trades if net(t) < 0),
            eod_wins=sum(1 for t in eod_trades if net(t) > 0),
            eod_losses=sum(1 for t in eod_trades if net(t) < 0),
            r_multiples=r_list,
            first_date=valid[0].entry_time.date().isoformat(),
            last_date=valid[-1].entry_time.date().isoformat(),
            winning_trades=len(wins),
            losing_trades=len(losses),
            breakeven_trades=len(breakeven),
            r_sample_size=len(r_list),
            dropped_trade_count=dropped,
            gross_pnl=gross_pnl,
            total_costs=total_costs,
        )

    @staticmethod
    def _calc_by_year(trades: List[Trade]) -> Dict[int, ConvexityMetrics]:
        by_year: Dict[int, List[Trade]] = {}
        for t in trades:
            y = t.entry_time.year
            by_year.setdefault(y, []).append(t)
        return {
            y: PerformanceAnalyzer._calc_convexity(ts)
            for y, ts in sorted(by_year.items())
        }

    @staticmethod
    def _calc_equity(
        equity_curve: List[Tuple[datetime, float]],
        initial_capital: float,
    ) -> EquityMetrics:
        if not equity_curve:
            empty = pd.Series(dtype=float)
            return EquityMetrics(
                total_return=0.0, annualized_return=0.0, sharpe_ratio=0.0,
                max_drawdown=0.0, max_drawdown_duration_days=0,
                equity_curve=empty, drawdown_curve=empty,
                bars_per_session=0.0, n_sessions=0,
            )

        times, values = zip(*equity_curve)
        eq = pd.Series(values, index=pd.DatetimeIndex(times))

        total_return = (eq.iloc[-1] - initial_capital) / initial_capital
        days = (eq.index[-1] - eq.index[0]).days or 1
        ann_return = (1 + total_return) ** (365 / days) - 1

        # Session closes. An intraday strategy is flat overnight, so the
        # meaningful return series is one observation per trading day.
        # Annualizing per-bar returns with a hardcoded bars-per-day constant
        # inflated Sharpe and produced a number not comparable to any
        # published figure.
        session_close = eq.resample("1D").last().dropna()
        session_counts = eq.resample("1D").count()
        session_counts = session_counts[session_counts > 0]
        n_sessions = int(len(session_counts))
        bars_per_session = (
            float(session_counts.median()) if n_sessions else 0.0
        )

        sharpe = 0.0
        session_returns = session_close.pct_change().dropna()
        if len(session_returns) > 1 and session_returns.std() > 0:
            sharpe = float(
                session_returns.mean() / session_returns.std() * np.sqrt(252)
            )

        # Drawdown stays on the full bar-level curve: an intraday trough is a
        # real loss of capital even when the session closes flat, and
        # measuring it on session closes only would understate it.
        roll_max = eq.cummax()
        drawdown = (eq - roll_max) / roll_max
        max_dd = float(drawdown.min())

        max_dd_days = PerformanceAnalyzer._max_drawdown_duration_days(drawdown)

        return EquityMetrics(
            total_return=total_return,
            annualized_return=ann_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_duration_days=max_dd_days,
            equity_curve=eq,
            drawdown_curve=drawdown,
            bars_per_session=bars_per_session,
            n_sessions=n_sessions,
        )

    @staticmethod
    def _max_drawdown_duration_days(drawdown: pd.Series) -> int:
        """Longest span spent below a prior peak, in calendar days.

        Measured from timestamps rather than by counting bars. The previous
        implementation counted bars and then applied ``* 5 // (78 * 5)``,
        which is integer division by the bars in a session; for any drawdown
        shorter than a full session that yields zero, and an ``or`` fallback
        then substituted the raw bar count. The result was a value that was
        sometimes days and sometimes bars, with no way to tell which.
        """
        if drawdown.empty:
            return 0

        longest = pd.Timedelta(0)
        start: pd.Timestamp | None = None

        for timestamp, value in drawdown.items():
            if value < 0:
                if start is None:
                    start = timestamp
            elif start is not None:
                longest = max(longest, timestamp - start)
                start = None

        if start is not None:
            # Still underwater at the end of the series; the drawdown has not
            # recovered, so it is measured to the final observation.
            longest = max(longest, drawdown.index[-1] - start)

        return int(longest.days)
