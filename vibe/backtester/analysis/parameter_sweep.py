"""
Generic parameter sensitivity testing framework for backtesting strategies.

Supports testing parameter combinations across any ruleset configuration.
"""
import itertools
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.analysis.metrics import BacktestResult
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
        
        return {
            **self.params,  # Parameter values
            "n_trades": metrics.n_trades,
            "win_rate": metrics.win_rate,
            "expectancy_r": metrics.expectancy_r,
            "total_pnl": metrics.total_pnl,
            "max_drawdown": equity.max_drawdown,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "sharpe_ratio": equity.sharpe_ratio,
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
    ) -> pd.DataFrame:
        """
        Run parameter sweep across all combinations.
        
        Args:
            symbol: Symbol to backtest
            start_date: Start date for backtest
            end_date: End date for backtest
            progress_callback: Optional callback(current, total, params) for progress updates
            
        Returns:
            DataFrame with results for all parameter combinations
        """
        combinations = self._generate_combinations()
        total = len(combinations)
        
        logger.info(f"Running parameter sweep: {total} combinations")
        logger.info(f"Parameters: {[p.name for p in self.parameters]}")
        logger.info(f"Symbol: {symbol}, Period: {start_date.date()} to {end_date.date()}")
        
        results = []
        
        for i, params in enumerate(combinations, 1):
            logger.info(f"[{i}/{total}] Testing: {params}")
            
            if progress_callback:
                progress_callback(i, total, params)
            
            try:
                # Create modified ruleset
                ruleset = self._create_modified_ruleset(params)
                
                # Run backtest
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
                )
                
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
        
        # Sort by total_pnl descending
        df = df.sort_values("total_pnl", ascending=False).reset_index(drop=True)
        
        logger.info(f"Parameter sweep complete: {len(results)}/{total} successful")
        
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
