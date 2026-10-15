"""
Fixed-parameter regime comparison across rolling date windows.

.. deprecated::
   Despite its name, this module does **not** perform walk-forward
   optimization. :meth:`WalkForwardEngine.analyze` holds ``self.ruleset``
   fixed and runs both the "train" and "test" window with identical
   parameters, so nothing is ever selected and the "test" window is not
   out-of-sample in any meaningful sense. Its caller
   (``vibe/backtester/optimization/pipeline.py``) compounds this by passing
   parameters chosen by a sweep over the *entire* date range, which places
   every test window inside the parameter-selection data.

   What it actually measures is how one fixed parameter set performs across
   different market regimes. That is a legitimate and useful question -- it is
   simply not the overfitting question the name implies.

   For genuine nested walk-forward with per-fold optimization, purge/embargo,
   and a held-out test window, use
   :class:`vibe.research_pipeline.walk_forward.NestedWalkForwardDriver` via
   ``scripts/run_nested_walk_forward.py``.
"""
import logging
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.data.paths import resolve_market_data_dir
from vibe.common.ruleset.models import StrategyRuleSet

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardPeriod:
    """Single train/test period in walk-forward analysis."""
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_result: Optional[BacktestResult] = None
    test_result: Optional[BacktestResult] = None
    
    @property
    def train_expectancy(self) -> float:
        return self.train_result.overall.expectancy_r if self.train_result else 0.0
    
    @property
    def test_expectancy(self) -> float:
        return self.test_result.overall.expectancy_r if self.test_result else 0.0
    
    @property
    def degradation(self) -> float:
        """Test performance relative to train (1.0 = no degradation).

        Returns 0.0 when training expectancy is too close to zero to divide
        by. A near-zero denominator produces an enormous ratio that reads as
        a spectacular result when it actually means the training window
        barely made anything, so the ratio is withheld rather than reported.
        """
        if abs(self.train_expectancy) < 1e-6:
            return 0.0
        return self.test_expectancy / self.train_expectancy


@dataclass
class WalkForwardAnalysis:
    """Summary of a fixed-parameter comparison across rolling windows.

    Not a walk-forward result: no parameter was selected, so the "test"
    windows are not out-of-sample. See the module docstring.
    """
    periods: List[WalkForwardPeriod]
    
    # Aggregate metrics
    avg_train_expectancy: float
    avg_test_expectancy: float
    avg_degradation: float
    
    # Composite score. Despite the name this does NOT measure out-of-sample
    # robustness, because the same parameters ran in both windows.
    walk_forward_score: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Export summary as dictionary.

        Carries ``is_out_of_sample: False`` so that any artifact derived from
        this object states what it is. Without the marker a consumer reading
        ``walk_forward_score`` has no way to tell this apart from a genuine
        nested walk-forward result, which is precisely how the defect
        survived this long.
        """
        return {
            "is_out_of_sample": False,
            "parameters_selected_per_fold": False,
            "method": "fixed_parameter_regime_comparison",
            "n_periods": len(self.periods),
            "avg_train_expectancy": self.avg_train_expectancy,
            "avg_test_expectancy": self.avg_test_expectancy,
            "avg_degradation": self.avg_degradation,
            "walk_forward_score": self.walk_forward_score,
            "periods": [
                {
                    "train_start": p.train_start.isoformat(),
                    "train_end": p.train_end.isoformat(),
                    "test_start": p.test_start.isoformat(),
                    "test_end": p.test_end.isoformat(),
                    "train_exp": p.train_expectancy,
                    "test_exp": p.test_expectancy,
                    "degradation": p.degradation,
                }
                for p in self.periods
            ],
        }


class WalkForwardEngine:
    """
    Perform walk-forward analysis on a strategy.
    
    Splits historical data into rolling train/test periods to evaluate
    out-of-sample performance and detect overfitting.
    
    Usage:
        engine = WalkForwardEngine(
            ruleset=ruleset,
            data_dir=Path("vibe/data/parquet"),
            initial_capital=10_000.0,
        )
        
        analysis = engine.analyze(
            symbol="QQQ",
            start_date=datetime(2020, 1, 1),
            end_date=datetime(2024, 12, 31),
            train_months=6,  # 6-month training window
            test_months=1,   # 1-month test window
            step_months=1,   # Roll forward by 1 month
        )
        
        print(f"Walk-forward score: {analysis.walk_forward_score:.2f}")
        print(f"Average degradation: {analysis.avg_degradation:.2%}")
    """
    
    def __init__(
        self,
        ruleset: StrategyRuleSet,
        data_dir: Path | str | None = None,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 5,
        execution_realism: ExecutionRealismConfig | None = None,
        run_sink: Any = None,
    ):
        self.ruleset = ruleset
        self.data_dir = resolve_market_data_dir(data_dir)
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks
        self.execution_realism = execution_realism or ExecutionRealismConfig.realistic()
        self.run_sink = run_sink
    
    def analyze(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        train_months: int = 6,
        test_months: int = 1,
        step_months: int = 1,
        precomputed_features: Optional[pd.DataFrame] = None,
    ) -> WalkForwardAnalysis:
        """
        Run the same fixed ruleset across rolling train/test windows.

        .. deprecated::
           This does not optimize. ``self.ruleset`` is used unchanged in both
           windows, so the "test" result is not out-of-sample. Use
           ``scripts/run_nested_walk_forward.py`` for real walk-forward.

        Args:
            symbol: Trading symbol
            start_date: Overall backtest start
            end_date: Overall backtest end
            train_months: Training window size in months
            test_months: Test window size in months
            step_months: Roll-forward step size in months
            precomputed_features: Optional pre-computed indicators
        
        Returns:
            WalkForwardAnalysis with summary metrics
        """
        warnings.warn(
            "WalkForwardEngine.analyze() does not select parameters: the same "
            "ruleset runs in both the train and test window, so its results "
            "are NOT out-of-sample evidence and must not be used to judge "
            "overfitting. Use NestedWalkForwardDriver "
            "(scripts/run_nested_walk_forward.py) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            "Fixed-parameter regime comparison on %s -- NOT out-of-sample.",
            symbol,
        )
        logger.info(f"  Train: {train_months}mo, Test: {test_months}mo, Step: {step_months}mo")
        
        # Generate periods
        periods = self._generate_periods(
            start_date, end_date, 
            train_months, test_months, step_months
        )
        
        logger.info(f"  Generated {len(periods)} train/test periods")
        
        # Run backtests for each period
        for i, period in enumerate(periods, 1):
            logger.info(f"  [{i}/{len(periods)}] Train: {period.train_start.date()} to {period.train_end.date()}, "
                       f"Test: {period.test_start.date()} to {period.test_end.date()}")
            
            # Run train backtest
            engine = BacktestEngine(
                ruleset=self.ruleset,
                data_dir=self.data_dir,
                initial_capital=self.initial_capital,
                slippage_ticks=self.slippage_ticks,
                execution_realism=self.execution_realism,
            )
            
            # Use subset of pre-computed features if provided
            train_features = self._subset_features(precomputed_features, period.train_start, period.train_end)
            test_features = self._subset_features(precomputed_features, period.test_start, period.test_end)
            
            period.train_result = engine.run(
                symbol=symbol,
                start_date=period.train_start,
                end_date=period.train_end,
                precomputed_features=train_features,
            )
            
            # Run test backtest
            period.test_result = engine.run(
                symbol=symbol,
                start_date=period.test_start,
                end_date=period.test_end,
                precomputed_features=test_features,
            )
            
            logger.info(f"    Train exp: {period.train_expectancy:.3f}R, "
                       f"Test exp: {period.test_expectancy:.3f}R, "
                       f"Degradation: {period.degradation:.1%}")

            self._record_fold(period, symbol=symbol, fold=i, total=len(periods))

        if self.run_sink is not None:
            logger.info(f"  Research store: {self.run_sink.summary()}")
        
        # Calculate aggregate metrics
        train_expectancies = [p.train_expectancy for p in periods]
        test_expectancies = [p.test_expectancy for p in periods]
        degradations = [p.degradation for p in periods if p.train_expectancy != 0]
        
        avg_train_expectancy = np.mean(train_expectancies)
        avg_test_expectancy = np.mean(test_expectancies)
        avg_degradation = np.mean(degradations) if degradations else 0.0
        
        # Calculate walk-forward score (0-1, higher = better)
        # Score based on:
        # 1. Positive test performance (50% weight)
        # 2. Low degradation (50% weight)
        
        if avg_test_expectancy > 0:
            test_score = min(avg_test_expectancy / 0.2, 1.0)  # 0.2R = excellent
        else:
            test_score = 0.0
        
        # Degradation score: 1.0 = no degradation, 0.5 = 50% degradation
        if avg_degradation >= 0:
            degradation_score = min(avg_degradation, 1.0)
        else:
            degradation_score = 0.0
        
        walk_forward_score = 0.5 * test_score + 0.5 * degradation_score
        
        logger.info(f"  ✓ Walk-forward score: {walk_forward_score:.2f}")
        logger.info(f"    Avg train: {avg_train_expectancy:.3f}R, Avg test: {avg_test_expectancy:.3f}R")
        logger.info(f"    Avg degradation: {avg_degradation:.1%}")
        
        return WalkForwardAnalysis(
            periods=periods,
            avg_train_expectancy=avg_train_expectancy,
            avg_test_expectancy=avg_test_expectancy,
            avg_degradation=avg_degradation,
            walk_forward_score=walk_forward_score,
        )
    
    def _generate_periods(
        self,
        start_date: datetime,
        end_date: datetime,
        train_months: int,
        test_months: int,
        step_months: int,
    ) -> List[WalkForwardPeriod]:
        """Generate train/test period splits.

        Windows are sized as ``months * 30`` calendar days, which drifts
        against the real calendar and makes no attempt to count trading
        sessions, so two folds nominally the same size can contain different
        numbers of opportunities. The session-based
        :class:`~vibe.research_pipeline.splits.planner.TemporalSplitPlanner`
        is the replacement; this is left as-is rather than repaired because
        the surrounding method is deprecated.
        """
        periods = []
        current_train_start = start_date
        
        while True:
            # Calculate period boundaries
            train_end = current_train_start + timedelta(days=train_months * 30)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=test_months * 30)
            
            # Stop if test period exceeds available data
            if test_end > end_date:
                break
            
            period = WalkForwardPeriod(
                train_start=current_train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
            periods.append(period)
            
            # Roll forward
            current_train_start = current_train_start + timedelta(days=step_months * 30)
        
        return periods
    
    def _record_fold(
        self,
        period: WalkForwardPeriod,
        *,
        symbol: str,
        fold: int,
        total: int,
    ) -> None:
        """Record both halves of one fold as separate research runs.

        Both halves are recorded as :attr:`SegmentRole.TRAIN`, because both
        halves *are* in-sample. The caller
        (``vibe/backtester/optimization/pipeline.py``) selects parameters with
        a sweep over the entire date range, so the window this engine calls
        "test" was part of the selection data. Recording it as
        :attr:`SegmentRole.TEST` would let an in-sample number be read as
        out-of-sample evidence -- precisely the confusion walk-forward
        analysis exists to prevent, and the one a research store must never
        encode.

        The two fingerprints stay distinct because the halves span different
        windows, which gives them different ``split_manifest_hash`` values.

        Genuine out-of-sample rows come from
        :class:`vibe.research_pipeline.walk_forward.NestedWalkForwardDriver`.
        """
        if self.run_sink is None:
            return

        from vibe.research_pipeline.contracts import SegmentRole

        halves = (
            (period.train_result, "train"),
            (period.test_result, "later-window (in-sample)"),
        )
        for result, label in halves:
            if result is None:
                continue
            self.run_sink.record(
                result,
                ruleset=self.ruleset,
                parameters={
                    "symbol": symbol,
                    "initial_capital": self.initial_capital,
                    "slippage_ticks": self.slippage_ticks,
                    "regime_comparison_half": label,
                },
                segment_role=SegmentRole.TRAIN,
                notes=(
                    f"Fixed-parameter regime comparison fold {fold}/{total} "
                    f"({label}) -- NOT out-of-sample"
                ),
            )

    def _subset_features(
        self,
        features: Optional[pd.DataFrame],
        start: datetime,
        end: datetime,
    ) -> Optional[pd.DataFrame]:
        """Extract subset of pre-computed features for a date range."""
        if features is None:
            return None
        
        mask = (features.index >= start) & (features.index <= end)
        return features[mask]
