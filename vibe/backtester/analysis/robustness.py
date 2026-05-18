"""
Robustness analysis for trading strategies.

Tests strategy stability under perturbations:
- Noise injection (randomize entry/exit prices)
- Parameter wiggling (vary params by ±X%)
- Random sub-sampling (test on random date subsets)
"""
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

import numpy as np
import pandas as pd

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.common.ruleset.models import StrategyRuleSet

logger = logging.getLogger(__name__)


@dataclass
class RobustnessTestResult:
    """Result from a single robustness test run."""
    test_type: str  # "noise_injection", "param_perturbation", "subsample"
    variation: float  # e.g., slippage_ticks or perturbation %
    result: BacktestResult
    
    @property
    def expectancy_r(self) -> float:
        return self.result.overall.expectancy_r
    
    @property
    def sharpe_ratio(self) -> float:
        return self.result.equity.sharpe_ratio
    
    @property
    def total_pnl(self) -> float:
        return self.result.overall.total_pnl


@dataclass
class RobustnessAnalysis:
    """Summary of robustness test results."""
    baseline_result: BacktestResult
    test_results: List[RobustnessTestResult]
    
    # Variance metrics (lower = more robust)
    expectancy_std: float
    sharpe_std: float
    pnl_std: float
    
    # Robustness score (0-1, higher = more robust)
    robustness_score: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Export summary as dictionary."""
        return {
            "baseline_expectancy_r": self.baseline_result.overall.expectancy_r,
            "baseline_sharpe": self.baseline_result.equity.sharpe_ratio,
            "baseline_pnl": self.baseline_result.overall.total_pnl,
            "n_tests": len(self.test_results),
            "expectancy_std": self.expectancy_std,
            "sharpe_std": self.sharpe_std,
            "pnl_std": self.pnl_std,
            "robustness_score": self.robustness_score,
        }


class RobustnessAnalyzer:
    """
    Test strategy robustness under various perturbations.
    
    Usage:
        analyzer = RobustnessAnalyzer(
            ruleset=ruleset,
            data_dir=Path("vibe/data/parquet"),
            initial_capital=10_000.0,
        )
        
        analysis = analyzer.analyze(
            symbol="QQQ",
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2024, 12, 31),
            noise_tests=10,  # Run 10 noise injection tests
            perturbation_tests=5,  # Run 5 parameter perturbation tests
        )
        
        print(f"Robustness score: {analysis.robustness_score:.2f}")
    """
    
    def __init__(
        self,
        ruleset: StrategyRuleSet,
        data_dir: Path,
        initial_capital: float = 10_000.0,
        baseline_slippage_ticks: int = 5,
    ):
        self.ruleset = ruleset
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.baseline_slippage_ticks = baseline_slippage_ticks
    
    def analyze(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        noise_tests: int = 10,
        perturbation_tests: int = 5,
        subsample_tests: int = 0,  # Future: random date subsets
        precomputed_features: Optional[pd.DataFrame] = None,
    ) -> RobustnessAnalysis:
        """
        Run robustness analysis.
        
        Args:
            symbol: Trading symbol
            start_date: Backtest start
            end_date: Backtest end
            noise_tests: Number of noise injection tests (vary slippage)
            perturbation_tests: Number of parameter perturbation tests
            subsample_tests: Number of random subsample tests (future)
            precomputed_features: Optional pre-computed indicators
        
        Returns:
            RobustnessAnalysis with summary metrics
        """
        logger.info(f"Running robustness analysis on {symbol}...")
        
        # 1. Run baseline
        baseline_engine = BacktestEngine(
            ruleset=self.ruleset,
            data_dir=self.data_dir,
            initial_capital=self.initial_capital,
            slippage_ticks=self.baseline_slippage_ticks,
        )
        
        baseline_result = baseline_engine.run(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            precomputed_features=precomputed_features,
        )
        
        logger.info(f"  Baseline: {baseline_result.overall.expectancy_r:.3f}R expectancy, "
                   f"{baseline_result.equity.sharpe_ratio:.2f} Sharpe")
        
        test_results = []
        
        # 2. Noise injection tests (vary slippage)
        if noise_tests > 0:
            logger.info(f"  Running {noise_tests} noise injection tests...")
            test_results.extend(
                self._run_noise_tests(
                    symbol, start_date, end_date, 
                    n_tests=noise_tests,
                    precomputed_features=precomputed_features
                )
            )
        
        # 3. Parameter perturbation tests (future: wiggle params by ±10%)
        if perturbation_tests > 0:
            logger.info(f"  Skipping {perturbation_tests} parameter perturbation tests (not yet implemented)")
            # TODO: Implement parameter perturbation
        
        # 4. Random subsample tests (future)
        if subsample_tests > 0:
            logger.info(f"  Skipping {subsample_tests} subsample tests (not yet implemented)")
            # TODO: Implement random date subsampling
        
        # Calculate variance metrics
        expectancies = [t.expectancy_r for t in test_results]
        sharpes = [t.sharpe_ratio for t in test_results]
        pnls = [t.total_pnl for t in test_results]
        
        expectancy_std = np.std(expectancies) if expectancies else 0.0
        sharpe_std = np.std(sharpes) if sharpes else 0.0
        pnl_std = np.std(pnls) if pnls else 0.0
        
        # Calculate robustness score (0-1, higher = more robust)
        # Penalize high variance relative to baseline performance
        baseline_exp = baseline_result.overall.expectancy_r
        
        if baseline_exp > 0 and expectancy_std > 0:
            # Coefficient of variation: std / mean
            cv = expectancy_std / abs(baseline_exp)
            # Score: 1 / (1 + cv) — lower variance = higher score
            robustness_score = 1.0 / (1.0 + cv)
        else:
            robustness_score = 0.0
        
        logger.info(f"  ✓ Robustness score: {robustness_score:.2f} "
                   f"(expectancy std: {expectancy_std:.3f}R)")
        
        return RobustnessAnalysis(
            baseline_result=baseline_result,
            test_results=test_results,
            expectancy_std=expectancy_std,
            sharpe_std=sharpe_std,
            pnl_std=pnl_std,
            robustness_score=robustness_score,
        )
    
    def _run_noise_tests(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        n_tests: int,
        precomputed_features: Optional[pd.DataFrame],
    ) -> List[RobustnessTestResult]:
        """Run noise injection tests by varying slippage."""
        results = []
        
        # Test slippage from baseline +/- 50%
        min_slippage = max(1, int(self.baseline_slippage_ticks * 0.5))
        max_slippage = int(self.baseline_slippage_ticks * 1.5)
        
        slippage_values = np.linspace(min_slippage, max_slippage, n_tests, dtype=int)
        
        for slippage in slippage_values:
            engine = BacktestEngine(
                ruleset=self.ruleset,
                data_dir=self.data_dir,
                initial_capital=self.initial_capital,
                slippage_ticks=int(slippage),
            )
            
            result = engine.run(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                precomputed_features=precomputed_features,
            )
            
            test_result = RobustnessTestResult(
                test_type="noise_injection",
                variation=float(slippage),
                result=result,
            )
            
            results.append(test_result)
            
            logger.info(f"    Slippage {slippage} ticks: {result.overall.expectancy_r:.3f}R")
        
        return results
