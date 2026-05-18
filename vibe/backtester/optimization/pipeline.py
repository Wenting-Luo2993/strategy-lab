"""
Unified optimization pipeline for trading strategies.

Integrates all optimization components:
- Parameter sweeping with pre-computed indicators
- Composite scoring with tail risk metrics
- Robustness analysis (noise injection)
- Walk-forward validation
- Surface analysis (cliff/plateau detection)
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

import pandas as pd

from vibe.backtester.analysis.parameter_sweep import ParameterSweep, ParameterDefinition
from vibe.backtester.analysis.robustness import RobustnessAnalyzer, RobustnessAnalysis
from vibe.backtester.analysis.walk_forward import WalkForwardEngine, WalkForwardAnalysis
from vibe.backtester.analysis.surface import SurfaceAnalyzer, ParameterSurface
from vibe.backtester.analysis.scoring import rank_results
from vibe.common.ruleset.models import StrategyRuleSet

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """
    Comprehensive optimization result.
    
    Includes:
    - Parameter sweep results
    - Best candidate parameters
    - Robustness analysis
    - Walk-forward analysis
    - Surface analysis (for 2D parameter pairs)
    """
    sweep_results: pd.DataFrame
    best_params: Dict[str, Any]
    best_score: float
    
    robustness_analysis: Optional[RobustnessAnalysis] = None
    walk_forward_analysis: Optional[WalkForwardAnalysis] = None
    surface_analysis: Optional[Dict[str, ParameterSurface]] = None
    
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 80,
            "OPTIMIZATION RESULT SUMMARY",
            "=" * 80,
            "",
            "Best Parameters:",
        ]
        
        for key, value in self.best_params.items():
            lines.append(f"  {key}: {value}")
        
        lines.append(f"\nComposite Score: {self.best_score:.3f}")
        
        if self.robustness_analysis:
            lines.append(f"\nRobustness Score: {self.robustness_analysis.robustness_score:.3f}")
            lines.append(f"  Expectancy Std: ±{self.robustness_analysis.expectancy_std:.3f}R")
        
        if self.walk_forward_analysis:
            lines.append(f"\nWalk-Forward Score: {self.walk_forward_analysis.walk_forward_score:.3f}")
            lines.append(f"  Avg Test Expectancy: {self.walk_forward_analysis.avg_test_expectancy:.3f}R")
            lines.append(f"  Avg Degradation: {self.walk_forward_analysis.avg_degradation:.1%}")
        
        if self.surface_analysis:
            lines.append(f"\nSurface Analysis:")
            for name, surface in self.surface_analysis.items():
                lines.append(f"  {name}:")
                cliffs = surface.detect_cliffs()
                plateaus = surface.detect_plateaus()
                lines.append(f"    Cliffs detected: {len(cliffs)}")
                lines.append(f"    Plateaus detected: {len(plateaus)}")
        
        lines.append("")
        lines.append("=" * 80)
        
        return "\n".join(lines)


class OptimizationPipeline:
    """
    Complete optimization pipeline for strategy parameter tuning.
    
    Features:
    - Pre-computed indicators (50-90% speedup)
    - Result caching (avoid re-running same params)
    - Multi-metric composite scoring
    - Robustness testing (noise injection)
    - Walk-forward validation
    - Parameter surface analysis
    
    Usage:
        pipeline = OptimizationPipeline(
            base_ruleset_path="vibe/rulesets/orb_production.yaml",
            data_dir=Path("vibe/data/parquet"),
        )
        
        result = pipeline.optimize(
            symbol="QQQ",
            start_date=datetime(2020, 1, 1),
            end_date=datetime(2024, 12, 31),
            parameters=[
                ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15]),
                ParameterDefinition("exit.take_profit.multiplier", [1.5, 2.0, 3.0]),
            ],
            run_robustness=True,
            run_walk_forward=True,
            run_surface=True,
        )
        
        print(result.summary())
    """
    
    def __init__(
        self,
        base_ruleset_path: Path | str,
        data_dir: Path | str,
        initial_capital: float = 10_000.0,
        slippage_ticks: int = 5,
    ):
        """
        Initialize optimization pipeline.
        
        Args:
            base_ruleset_path: Path to base ruleset YAML
            data_dir: Path to Parquet data directory
            initial_capital: Starting capital for backtests
            slippage_ticks: Slippage simulation (ticks)
        """
        self.base_ruleset_path = Path(base_ruleset_path)
        self.data_dir = Path(data_dir)
        self.initial_capital = initial_capital
        self.slippage_ticks = slippage_ticks
    
    def optimize(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        parameters: List[ParameterDefinition],
        sweep_mode: str = "grid",
        cache_dir: Optional[Path] = None,
        run_robustness: bool = False,
        run_walk_forward: bool = False,
        run_surface: bool = False,
        output_dir: Optional[Path] = None,
    ) -> OptimizationResult:
        """
        Run complete optimization pipeline.
        
        Args:
            symbol: Trading symbol
            start_date: Backtest start date
            end_date: Backtest end date
            parameters: List of parameters to optimize
            sweep_mode: "grid" or "one_at_a_time"
            cache_dir: Optional cache directory for results
            run_robustness: Run robustness analysis on best candidate
            run_walk_forward: Run walk-forward analysis on best candidate
            run_surface: Run surface analysis for 2D parameter pairs
            output_dir: Optional directory for reports/plots
        
        Returns:
            OptimizationResult with comprehensive analysis
        """
        logger.info("=" * 80)
        logger.info("OPTIMIZATION PIPELINE START")
        logger.info("=" * 80)
        logger.info(f"Symbol: {symbol}")
        logger.info(f"Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"Parameters: {[p.name for p in parameters]}")
        logger.info(f"Sweep mode: {sweep_mode}")
        
        # Step 1: Parameter sweep with pre-computed features
        logger.info("\n[1/4] Running parameter sweep...")
        
        sweep = ParameterSweep(
            base_ruleset_path=self.base_ruleset_path,
            data_dir=self.data_dir,
            parameters=parameters,
            initial_capital=self.initial_capital,
            slippage_ticks=self.slippage_ticks,
            sweep_mode=sweep_mode,
        )
        
        sweep_results = sweep.run(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            use_precomputed_features=True,  # ← Key optimization!
            cache_dir=cache_dir,
        )
        
        logger.info(f"  ✓ Tested {len(sweep_results)} parameter combinations")
        
        # Get best candidate
        best_row = sweep_results.iloc[0]
        best_params = {p.name: best_row[p.name] for p in parameters}
        best_score = best_row["composite_score"]
        
        logger.info(f"\nBest parameters: {best_params}")
        logger.info(f"Composite score: {best_score:.3f}")
        
        # Save sweep results
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            sweep_results_path = output_dir / "parameter_sweep.csv"
            sweep.save_results(sweep_results, sweep_results_path)
            logger.info(f"  Saved sweep results to {sweep_results_path}")
        
        # Step 2: Robustness analysis (optional)
        robustness_analysis = None
        if run_robustness:
            logger.info("\n[2/4] Running robustness analysis...")
            
            # Load best ruleset
            best_ruleset = sweep._create_modified_ruleset(best_params)
            
            analyzer = RobustnessAnalyzer(
                ruleset=best_ruleset,
                data_dir=self.data_dir,
                initial_capital=self.initial_capital,
                baseline_slippage_ticks=self.slippage_ticks,
            )
            
            robustness_analysis = analyzer.analyze(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                noise_tests=10,
            )
            
            logger.info(f"  ✓ Robustness score: {robustness_analysis.robustness_score:.3f}")
        else:
            logger.info("\n[2/4] Skipping robustness analysis")
        
        # Step 3: Walk-forward analysis (optional)
        walk_forward_analysis = None
        if run_walk_forward:
            logger.info("\n[3/4] Running walk-forward analysis...")
            
            # Load best ruleset
            best_ruleset = sweep._create_modified_ruleset(best_params)
            
            wf_engine = WalkForwardEngine(
                ruleset=best_ruleset,
                data_dir=self.data_dir,
                initial_capital=self.initial_capital,
                slippage_ticks=self.slippage_ticks,
            )
            
            walk_forward_analysis = wf_engine.analyze(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                train_months=6,
                test_months=1,
                step_months=1,
            )
            
            logger.info(f"  ✓ Walk-forward score: {walk_forward_analysis.walk_forward_score:.3f}")
        else:
            logger.info("\n[3/4] Skipping walk-forward analysis")
        
        # Step 4: Surface analysis (optional, for 2D parameter pairs)
        surface_analysis = None
        if run_surface and len(parameters) >= 2:
            logger.info("\n[4/4] Running surface analysis...")
            
            surface_analyzer = SurfaceAnalyzer()
            surface_analysis = {}
            
            # Analyze first two parameters
            param_x = parameters[0].name
            param_y = parameters[1].name
            
            surface = surface_analyzer.create_surface(
                results_df=sweep_results,
                param_x=param_x,
                param_y=param_y,
                metric="composite_score",
            )
            
            surface_analysis[f"{param_x}_vs_{param_y}"] = surface
            
            # Generate summary
            summary = surface_analyzer.summary_report(surface)
            logger.info(f"  ✓ Surface: {summary['grid_size']} grid")
            logger.info(f"    Cliffs: {summary['n_cliffs']}, Plateaus: {summary['n_plateaus']}")
            
            # Plot surface
            if output_dir:
                plot_path = output_dir / f"surface_{param_x}_vs_{param_y}.png"
                surface_analyzer.plot_surface(surface, output_path=str(plot_path))
        else:
            logger.info("\n[4/4] Skipping surface analysis")
        
        # Create result
        result = OptimizationResult(
            sweep_results=sweep_results,
            best_params=best_params,
            best_score=best_score,
            robustness_analysis=robustness_analysis,
            walk_forward_analysis=walk_forward_analysis,
            surface_analysis=surface_analysis,
        )
        
        logger.info("\n" + "=" * 80)
        logger.info("OPTIMIZATION PIPELINE COMPLETE")
        logger.info("=" * 80)
        
        return result
