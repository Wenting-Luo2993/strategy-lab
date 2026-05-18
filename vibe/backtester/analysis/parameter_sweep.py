"""
Generic parameter sensitivity testing framework for backtesting strategies.

Supports testing parameter combinations across any ruleset configuration.
"""
import asyncio
import hashlib
import itertools
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from vibe.backtester.core.engine import BacktestEngine, _resample
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.analysis.regime_research.features import FeatureEngine
from vibe.backtester.analysis.scoring import composite_score, calculate_tail_ratio
from vibe.backtester.data.parquet_loader import ParquetLoader
from vibe.common.ruleset.models import StrategyRuleSet

logger = logging.getLogger(__name__)


@dataclass
class ParameterDefinition:
    """Defines a parameter to sweep and its test values.
    
    Args:
        path: Dot-separated path in YAML (e.g., "strategy.orb_duration_minutes")
        values: List of values to test for this parameter
        name: Human-readable name for reporting (optional, defaults to last path component)
        base_value: Default value to use when not sweeping this parameter (one-at-a-time mode)
    """
    path: str
    values: List[Any]
    name: Optional[str] = None
    base_value: Optional[Any] = None
    
    def __post_init__(self):
        if self.name is None:
            self.name = self.path.split(".")[-1]
        # If no base_value specified, use first value in list
        if self.base_value is None and self.values:
            self.base_value = self.values[0]


@dataclass
class SweepResult:
    """Result from a single parameter combination test."""
    params: Dict[str, Any]
    result: BacktestResult
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to flat dictionary for DataFrame."""
        metrics = self.result.overall
        equity = self.result.equity
        
        # Calculate profit factor (gross profit / gross loss)
        wins = [t.pnl for t in self.result.trades if t.pnl > 0]
        losses = [t.pnl for t in self.result.trades if t.pnl < 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        # Calculate avg win/loss in dollars
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        
        # Calculate composite score and tail ratio
        score = composite_score(self.result)
        tail_ratio = calculate_tail_ratio(metrics.r_multiples)
        
        return {
            **self.params,  # Parameter values
            "composite_score": score,  # ← New: Multi-metric ranking score
            "n_trades": metrics.n_trades,
            "win_rate": metrics.win_rate,
            "expectancy_r": metrics.expectancy_r,
            "total_pnl": metrics.total_pnl,
            "max_drawdown": equity.max_drawdown,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "sharpe_ratio": equity.sharpe_ratio,
            "tail_ratio": tail_ratio,  # ← New: Convexity measure
        }


class ParameterSweep:
    """
    Generic parameter sensitivity testing framework.
    
    Features:
    - Tests all combinations of parameter values
    - Works with any ruleset YAML configuration
    - Supports nested parameter paths (e.g., "exit.take_profit.multiplier")
    - Generates detailed comparison reports
    
    Example:
        ```python
        sweep = ParameterSweep(
            base_ruleset_path="vibe/rulesets/orb_production.yaml",
            data_dir=Path("vibe/data/parquet"),
            parameters=[
                ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15]),
                ParameterDefinition("exit.take_profit.multiplier", [1.5, 2.0, 3.0]),
            ],
        )
        
        results = sweep.run(
            symbol="QQQ",
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2024, 12, 31),
        )
        
        sweep.save_results(results, "reports/sensitivity.csv")
        ```
    """
    
    def __init__(
        self,
        base_ruleset_path: Path | str,
        data_dir: Path | str,
        parameters: List[ParameterDefinition],
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 5,
        sweep_mode: str = "one_at_a_time",
    ):
        """
        Initialize parameter sweep.
        
        Args:
            base_ruleset_path: Path to base ruleset YAML file
            data_dir: Path to Parquet data directory
            parameters: List of parameters to sweep
            initial_capital: Starting capital for each backtest
            slippage_ticks: Slippage simulation (ticks)
            sweep_mode: "one_at_a_time" (vary one param at a time) or "grid" (Cartesian product)
        """
        self.base_ruleset_path = Path(base_ruleset_path)
        self.data_dir = Path(data_dir)
        self.parameters = parameters
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks
        self.sweep_mode = sweep_mode
        
        if sweep_mode not in ("one_at_a_time", "grid"):
            raise ValueError(f"Invalid sweep_mode: {sweep_mode}. Must be 'one_at_a_time' or 'grid'")
        
        # Load base ruleset YAML
        with open(self.base_ruleset_path, "r") as f:
            self.base_config = yaml.safe_load(f)
    
    def _precompute_features(
        self, 
        symbol: str, 
        start_date: datetime, 
        end_date: datetime,
        timeframe: str = "5m"
    ) -> pd.DataFrame:
        """
        Pre-compute technical indicators for the entire date range.
        
        This is a CRITICAL optimization: compute indicators ONCE instead of
        recalculating on every parameter combination.
        
        Args:
            symbol: Trading symbol
            start_date: Start date
            end_date: End date
            timeframe: Bar timeframe (e.g., "5m", "15m")
        
        Returns:
            DataFrame with indicators indexed by timestamp
        """
        logger.info(f"Pre-computing features for {symbol} ({start_date.date()} to {end_date.date()})...")
        
        # Load 1-minute data
        loader = ParquetLoader(self.data_dir, [symbol])
        df_1m = asyncio.run(
            loader.get_bars(symbol, start_time=start_date, end_time=end_date)
        )
        
        # Resample to target timeframe
        pd_interval = timeframe.replace("m", "min")
        df = _resample(df_1m, pd_interval)
        
        # Compute all features using FeatureEngine
        feature_engine = FeatureEngine()
        features = feature_engine.compute(
            df, 
            features=["atr_14", "atr_pctile", "adx_14", "slope_20d", "slope_50d"]
        )
        
        # Also add ATR_{period} column for backward compatibility
        # (ORB strategy expects ATR_14 column)
        if "atr_14" in features.columns:
            features["ATR_14"] = features["atr_14"]
        
        logger.info(f"  ✓ Computed {len(features.columns)} indicators for {len(features)} bars")
        
        return features
    
    def _cache_key(
        self, 
        params: Dict[str, Any], 
        symbol: str, 
        start_date: datetime, 
        end_date: datetime
    ) -> str:
        """
        Generate a unique cache key for a parameter combination.
        
        Args:
            params: Parameter values
            symbol: Trading symbol
            start_date: Backtest start
            end_date: Backtest end
        
        Returns:
            MD5 hash string
        """
        # Sort params for consistent hashing
        sorted_params = sorted(params.items())
        key_string = (
            f"{self.base_ruleset_path.name}_"
            f"{symbol}_"
            f"{start_date.isoformat()}_"
            f"{end_date.isoformat()}_"
            f"{sorted_params}_"
            f"capital_{self.initial_capital}_"
            f"slippage_{self.slippage_ticks}"
        )
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _get_cached_result(
        self, 
        cache_dir: Path, 
        cache_key: str
    ) -> Optional[BacktestResult]:
        """Load cached backtest result if it exists."""
        cache_file = cache_dir / f"{cache_key}.pkl"
        if cache_file.exists():
            try:
                with open(cache_file, "rb") as f:
                    return pickle.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache {cache_key}: {e}")
                return None
        return None
    
    def _save_cached_result(
        self, 
        cache_dir: Path, 
        cache_key: str, 
        result: BacktestResult
    ) -> None:
        """Save backtest result to cache."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{cache_key}.pkl"
        try:
            with open(cache_file, "wb") as f:
                pickle.dump(result, f)
        except Exception as e:
            logger.warning(f"Failed to save cache {cache_key}: {e}")
    
    def _set_nested_value(self, config: Dict[str, Any], path: str, value: Any) -> None:
        """Set a value in nested dictionary using dot-separated path.
        
        Args:
            config: Dictionary to modify
            path: Dot-separated path (e.g., "exit.take_profit.multiplier")
            value: Value to set
        """
        keys = path.split(".")
        current = config
        
        # Navigate to parent of target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        # Set the value
        current[keys[-1]] = value
    
    def _generate_combinations(self) -> List[Dict[str, Any]]:
        """Generate all parameter combinations to test.
        
        Returns:
            List of parameter dictionaries, one per combination
        """
        if self.sweep_mode == "grid":
            # Cartesian product: test all combinations
            param_names = [p.name for p in self.parameters]
            value_lists = [p.values for p in self.parameters]
            
            combinations = []
            for values in itertools.product(*value_lists):
                combo = dict(zip(param_names, values))
                combinations.append(combo)
            
            return combinations
        
        elif self.sweep_mode == "one_at_a_time":
            # One-at-a-time: vary each parameter while keeping others at base
            combinations = []
            
            # Create base combination
            base_combo = {p.name: p.base_value for p in self.parameters}
            
            # For each parameter, test each value
            for param_def in self.parameters:
                for value in param_def.values:
                    # Skip if this is already the base value (avoid duplicates)
                    if value == param_def.base_value:
                        continue
                    
                    # Create combination with this parameter varied
                    combo = base_combo.copy()
                    combo[param_def.name] = value
                    combinations.append(combo)
            
            # Add base combination at the start
            combinations.insert(0, base_combo)
            
            return combinations
        
        else:
            raise ValueError(f"Unknown sweep_mode: {self.sweep_mode}")
    
    def _create_modified_ruleset(self, params: Dict[str, Any]) -> StrategyRuleSet:
        """Create a modified ruleset with parameter values.
        
        Args:
            params: Parameter values to set (name -> value)
            
        Returns:
            StrategyRuleSet with modified parameters
        """
        # Deep copy base config
        import copy
        config = copy.deepcopy(self.base_config)
        
        # Apply parameter modifications
        for param_def in self.parameters:
            value = params[param_def.name]
            self._set_nested_value(config, param_def.path, value)
        
        # Convert to StrategyRuleSet
        return StrategyRuleSet(**config)
    
    def run(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        progress_callback: Optional[callable] = None,
        use_precomputed_features: bool = True,
        cache_dir: Optional[Path] = None,
    ) -> pd.DataFrame:
        """
        Run parameter sweep across all combinations.
        
        Args:
            symbol: Symbol to backtest
            start_date: Start date for backtest
            end_date: End date for backtest
            progress_callback: Optional callback(current, total, params) for progress updates
            use_precomputed_features: If True, pre-compute indicators once (50-90% speedup)
            cache_dir: Optional directory for caching results (avoids re-running same params)
            
        Returns:
            DataFrame with results for all parameter combinations
        """
        combinations = self._generate_combinations()
        total = len(combinations)
        
        logger.info(f"Running parameter sweep: {total} combinations")
        logger.info(f"Parameters: {[p.name for p in self.parameters]}")
        logger.info(f"Symbol: {symbol}, Period: {start_date.date()} to {end_date.date()}")
        
        # Enable caching if cache_dir provided
        if cache_dir:
            cache_dir = Path(cache_dir)
            logger.info(f"Result caching enabled: {cache_dir}")
        
        # Pre-compute features ONCE for massive performance gain
        precomputed_features = None
        if use_precomputed_features:
            # Get timeframe from base config (default to 5m)
            timeframe = self.base_config.get("instruments", {}).get("timeframe", "5m")
            precomputed_features = self._precompute_features(symbol, start_date, end_date, timeframe)
        
        results = []
        cache_hits = 0
        
        for i, params in enumerate(combinations, 1):
            logger.info(f"[{i}/{total}] Testing: {params}")
            
            if progress_callback:
                progress_callback(i, total, params)
            
            try:
                # Check cache first
                result = None
                if cache_dir:
                    cache_key = self._cache_key(params, symbol, start_date, end_date)
                    result = self._get_cached_result(cache_dir, cache_key)
                    if result:
                        cache_hits += 1
                        logger.info(f"  ✓ Loaded from cache ({cache_hits} hits so far)")
                
                # Run backtest if not cached
                if result is None:
                    # Create modified ruleset
                    ruleset = self._create_modified_ruleset(params)
                    
                    # Run backtest with pre-computed features
                    engine = BacktestEngine(
                        ruleset=ruleset,
                        data_dir=self.data_dir,
                        initial_capital=self.initial_capital,
                        slippage_ticks=self.slippage_ticks,
                    )
                    
                    result = engine.run(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        precomputed_features=precomputed_features,  # ← Key optimization!
                    )
                    
                    # Save to cache
                    if cache_dir:
                        self._save_cached_result(cache_dir, cache_key, result)
                
                # Store result
                sweep_result = SweepResult(params=params, result=result)
                results.append(sweep_result)
                
                metrics = result.overall
                logger.info(f"  → Trades: {metrics.n_trades}, Win%: {metrics.win_rate:.1%}, "
                           f"Exp: {metrics.expectancy_r:.2f}R, P&L: ${metrics.total_pnl:,.0f}")
                
            except Exception as e:
                logger.error(f"Failed for {params}: {e}")
                # Continue with next combination
        
        # Convert to DataFrame
        df = pd.DataFrame([r.to_dict() for r in results])
        
        # Sort by composite_score descending (multi-metric ranking)
        df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
        
        logger.info(f"Parameter sweep complete: {len(results)}/{total} successful")
        if cache_hits > 0:
            logger.info(f"Cache hits: {cache_hits}/{total} ({cache_hits/total:.1%})")
        
        return df
    
    def save_results(self, df: pd.DataFrame, output_path: Path | str) -> None:
        """Save results to CSV file.
        
        Args:
            df: Results DataFrame
            output_path: Path to save CSV
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        df.to_csv(output_path, index=False, float_format="%.4f")
        logger.info(f"Results saved to {output_path}")
    
    def print_summary(self, df: pd.DataFrame, top_n: int = 5) -> None:
        """Print summary of top-performing parameter combinations.
        
        Args:
            df: Results DataFrame
            top_n: Number of top results to show
        """
        print("\n" + "=" * 80)
        print(f"PARAMETER SENSITIVITY ANALYSIS - TOP {top_n} RESULTS")
        print("=" * 80)
        
        param_cols = [p.name for p in self.parameters]
        metric_cols = ["n_trades", "win_rate", "expectancy_r", "total_pnl", "max_drawdown"]
        
        # Format display
        display_df = df.head(top_n).copy()
        display_df["win_rate"] = display_df["win_rate"].apply(lambda x: f"{x:.1%}")
        display_df["expectancy_r"] = display_df["expectancy_r"].apply(lambda x: f"{x:.2f}R")
        display_df["total_pnl"] = display_df["total_pnl"].apply(lambda x: f"${x:,.0f}")
        display_df["max_drawdown"] = display_df["max_drawdown"].apply(lambda x: f"${x:,.0f}")
        
        print(display_df[param_cols + metric_cols].to_string(index=False))
        print("=" * 80 + "\n")
