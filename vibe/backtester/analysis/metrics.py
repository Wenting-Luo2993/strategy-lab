from dataclasses import dataclass, field
from typing import Dict, List
import pandas as pd
from vibe.common.models.trade import Trade


@dataclass
class ConvexityMetrics:
    """
    R-multiple based metrics — primary output for convexity analysis.
    R = trade_pnl / initial_risk_dollars.
    """
    n_trades: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    max_win_r: float
    max_loss_r: float
    top10_pct: float        # % of total profit from top 10% of trades
    skewness: float
    max_losing_streak: int
    total_pnl: float
    stop_wins: int
    stop_losses: int
    eod_wins: int
    eod_losses: int
    r_multiples: List[float]
    first_date: str
    last_date: str


@dataclass
class EquityMetrics:
    """Capital-curve metrics — equity/drawdown charting."""
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration_days: int
    equity_curve: pd.Series
    drawdown_curve: pd.Series


@dataclass
class BacktestResult:
    """Full result returned by BacktestEngine.run()."""
    overall: ConvexityMetrics
    by_year: Dict[int, ConvexityMetrics]
    equity: EquityMetrics
    trades: List[Trade]
    regime_breakdown: Dict[str, ConvexityMetrics]
    symbol: str
    start_date: str
    end_date: str
    ruleset_name: str
    ruleset_version: str
