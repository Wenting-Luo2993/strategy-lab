from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from vibe.backtester.analysis.metrics import (
    BacktestResult, ConvexityMetrics, EquityMetrics,
)
from vibe.common.models.trade import Trade


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
        )

    @staticmethod
    def _calc_convexity(trades: List[Trade]) -> ConvexityMetrics:
        valid = [t for t in trades if t.initial_risk and t.initial_risk > 0]
        if not valid:
            return ConvexityMetrics(
                n_trades=0, win_rate=0.0, avg_win_r=0.0, avg_loss_r=0.0,
                expectancy_r=0.0, max_win_r=0.0, max_loss_r=0.0,
                top10_pct=0.0, skewness=0.0, max_losing_streak=0,
                total_pnl=0.0, stop_wins=0, stop_losses=0,
                eod_wins=0, eod_losses=0, r_multiples=[],
                first_date="", last_date="",
            )

        r_list = [t.pnl / t.initial_risk for t in valid]
        wins   = [r for r in r_list if r > 0]
        losses = [r for r in r_list if r <= 0]
        wr = len(wins) / len(r_list)
        avg_win  = float(np.mean(wins))  if wins   else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        gross_profit = sum(t.pnl for t in valid if t.pnl > 0)
        top_n = max(1, len(valid) // 10)
        top_pnls = sorted([t.pnl for t in valid], reverse=True)[:top_n]
        top10_pct = (sum(top_pnls) / gross_profit * 100) if gross_profit > 0 else 0.0

        mean_r = float(np.mean(r_list))
        std_r  = float(np.std(r_list))
        skew = (float(np.mean([(r - mean_r) ** 3 for r in r_list])) / std_r ** 3
                if std_r > 0 else 0.0)

        streak = cur = 0
        for r in r_list:
            cur = cur + 1 if r <= 0 else 0
            streak = max(streak, cur)

        stop_trades = [t for t in valid if t.exit_reason == "STOP"]
        eod_trades  = [t for t in valid if t.exit_reason == "EOD"]

        return ConvexityMetrics(
            n_trades=len(r_list),
            win_rate=wr,
            avg_win_r=avg_win,
            avg_loss_r=avg_loss,
            expectancy_r=wr * avg_win + (1 - wr) * avg_loss,
            max_win_r=max(r_list),
            max_loss_r=min(r_list),
            top10_pct=top10_pct,
            skewness=skew,
            max_losing_streak=streak,
            total_pnl=sum(t.pnl for t in valid),
            stop_wins=sum(1 for t in stop_trades if t.pnl > 0),
            stop_losses=sum(1 for t in stop_trades if t.pnl <= 0),
            eod_wins=sum(1 for t in eod_trades if t.pnl > 0),
            eod_losses=sum(1 for t in eod_trades if t.pnl <= 0),
            r_multiples=r_list,
            first_date=valid[0].entry_time.date().isoformat(),
            last_date=valid[-1].entry_time.date().isoformat(),
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
            )

        times, values = zip(*equity_curve)
        eq = pd.Series(values, index=pd.DatetimeIndex(times))
        returns = eq.pct_change().dropna()

        total_return = (eq.iloc[-1] - initial_capital) / initial_capital
        days = (eq.index[-1] - eq.index[0]).days or 1
        ann_return = (1 + total_return) ** (365 / days) - 1

        sharpe = 0.0
        if returns.std() > 0:
            sharpe = float((returns.mean() / returns.std()) * np.sqrt(252 * 78))

        roll_max = eq.cummax()
        drawdown = (eq - roll_max) / roll_max
        max_dd = float(drawdown.min())

        in_dd = drawdown < 0
        max_dd_days = 0
        cur_dd = 0
        for v in in_dd:
            cur_dd = cur_dd + 1 if v else 0
            max_dd_days = max(max_dd_days, cur_dd)
        max_dd_days = max_dd_days * 5 // (78 * 5) or max_dd_days

        return EquityMetrics(
            total_return=total_return,
            annualized_return=ann_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_duration_days=max_dd_days,
            equity_curve=eq,
            drawdown_curve=drawdown,
        )
