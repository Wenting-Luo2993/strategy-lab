from dataclasses import dataclass, field
from typing import Dict, List
import pandas as pd
from vibe.common.models.trade import Trade

# Bump when any metric definition below changes meaning. Stored alongside
# results so numbers computed under different rules are never silently
# compared. Version 1 is the pre-normalization definition set; version 2 is
# the normalized set introduced by increment P1.
METRIC_CALCULATION_VERSION = 2


@dataclass
class ConvexityMetrics:
    """
    R-multiple based metrics — primary output for convexity analysis.
    R = trade_pnl / initial_risk_dollars.

    Definitions are fixed by increment P1 and must not be restated elsewhere:

    - A **win** is ``r > 0``, a **loss** is ``r < 0``, and ``r == 0`` is
      **breakeven** and belongs to neither. The three counts partition the R
      sample exactly, which is what makes
      ``winning + losing + breakeven == r_sample_size`` a real assertion
      against the ledger rather than a tautology between two derived metrics.
    - ``avg_loss_r`` averages strict losses only. Folding breakeven trades in
      would pull it toward zero and overstate the strategy.
    - ``n_trades`` counts **every** trade in the ledger. ``r_sample_size``
      counts those with a usable ``initial_risk``, and ``dropped_trade_count``
      is the difference. R-based statistics are computed over the R sample;
      cash statistics are computed over all trades. Previously trades with
      ``initial_risk <= 0`` vanished from both without trace.
    - ``total_pnl`` sums every trade, so it reconciles against the equity
      curve even when some trades carry no usable risk denominator.
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
    # Defaulted so existing constructors keep working; always populated by
    # PerformanceAnalyzer.
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    r_sample_size: int = 0
    dropped_trade_count: int = 0
    calculation_version: int = METRIC_CALCULATION_VERSION


@dataclass
class EquityMetrics:
    """Capital-curve metrics — equity/drawdown charting.

    Units and signs are declared here because they were previously ambiguous
    between modules:

    - ``max_drawdown`` is a **negative fraction** in ``[-1, 0]``. ``-0.16``
      means a 16% peak-to-trough decline. It is *not* dollars; a sweep
      renderer formatting it as currency was a display bug, not a second
      convention.
    - ``max_drawdown_duration_days`` is **calendar days** between the peak and
      its recovery, derived from timestamps. It was previously a bar count put
      through an integer division that collapsed to zero and then silently fell
      back to the raw bar count.
    - ``sharpe_ratio`` is annualized from **session** returns with
      ``sqrt(252)``. It is not a per-bar figure: an intraday curve is flat
      overnight, so per-bar annualization inflates the result and is not
      comparable to any published Sharpe.
    """
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration_days: int
    equity_curve: pd.Series
    drawdown_curve: pd.Series
    bars_per_session: float = 0.0
    n_sessions: int = 0
    calculation_version: int = METRIC_CALCULATION_VERSION


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
    execution_diagnostics: Dict[str, float] = field(default_factory=dict)
    """Always-recorded execution honesty counters.

    Populated regardless of whether realism behaviour is enabled, so a legacy
    run still reports how much of its result rests on ambiguous assumptions:
    ``ambiguous_exit_bars``, ``gap_through_exits``, ``min_cash``,
    ``max_gross_exposure_ratio``, ``execution_model_version``.
    """
