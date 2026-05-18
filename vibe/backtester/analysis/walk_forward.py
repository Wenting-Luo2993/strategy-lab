"""
Walk-forward analysis for trading strategies.

Evaluates strategy performance on rolling train/test splits to detect overfitting
and measure out-of-sample degradation.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.analysis.metrics import BacktestResult
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
        """Test performance relative to train (1.0 = no degradation)."""
        if self.train_expectancy == 0:
            return 0.0
        return self.test_expectancy / self.train_expectancy


@dataclass
class WalkForwardAnalysis:
    """Summary of walk-forward test results."""
    periods: List[WalkForwardPeriod]
    
    # Aggregate metrics
    avg_train_expectancy: float
    avg_test_expectancy: float
    avg_degradation: float
    
    # Walk-forward score (0-1, higher = better OOS performance)
    walk_forward_score: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Export summary as dictionary."""
        return {
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
        data_dir: Path,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 5,
    ):
        self.ruleset = ruleset
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks
    
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
        Run walk-forward analysis.
        
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
        logger.info(f"Running walk-forward analysis on {symbol}...")
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
        """Generate train/test period splits."""
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
