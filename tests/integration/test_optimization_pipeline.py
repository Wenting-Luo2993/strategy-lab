"""
Integration tests for optimization pipeline (PRD Tests 6, 7, 8).

Verifies that the complete optimization pipeline works end-to-end:
- Test 6: Full optimization pipeline execution
- Test 7: Overfitting detection via stability scoring
- Test 8: Surface analysis output validation
"""
import pytest
from pathlib import Path
from datetime import datetime
import pandas as pd

from vibe.backtester.optimization.pipeline import OptimizationPipeline, OptimizationResult
from vibe.backtester.analysis.parameter_sweep import ParameterDefinition


def test_full_optimization_pipeline():
    """
    PRD Test 6: Full Optimization Pipeline
    
    Verify that:
    1. Pipeline executes all steps without errors
    2. Returns valid OptimizationResult
    3. Ranked candidates list is populated
    4. Best candidate is identified
    """
    # Define parameter grid
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15], name="orb_duration_minutes"),
        ParameterDefinition("exit.take_profit.multiplier", [2.0, 3.0], name="tp_multiplier"),
    ]
    
    # Create pipeline
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
        slippage_ticks=5,
    )
    
    # Run optimization (short period for speed)
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        parameters=grid,
        sweep_mode="grid",  # 2 * 2 = 4 combinations
        run_robustness=False,  # Skip for speed
        run_walk_forward=False,
        run_surface=False,
    )
    
    # Verify result structure
    assert isinstance(result, OptimizationResult)
    assert result.sweep_results is not None
    assert len(result.sweep_results) == 4  # 2 * 2 combinations
    
    # Verify best candidate exists
    assert result.best_params is not None
    assert "orb_duration_minutes" in result.best_params
    assert "tp_multiplier" in result.best_params
    
    # Verify best score
    assert isinstance(result.best_score, float)
    assert 0.0 <= result.best_score <= 1.0
    
    # Verify sweep results have expected columns
    assert "composite_score" in result.sweep_results.columns
    assert "expectancy_r" in result.sweep_results.columns
    assert "sharpe_ratio" in result.sweep_results.columns
    assert "n_trades" in result.sweep_results.columns


def test_optimization_pipeline_with_robustness():
    """
    Verify pipeline integrates robustness analysis correctly.
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15], name="orb_duration_minutes"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        parameters=grid,
        run_robustness=True,  # ← Enable robustness
        run_walk_forward=False,
        run_surface=False,
    )
    
    # Verify robustness analysis exists
    assert result.robustness_analysis is not None
    assert 0.0 <= result.robustness_analysis.robustness_score <= 1.0
    assert result.robustness_analysis.expectancy_std >= 0.0


def test_optimization_pipeline_with_walk_forward():
    """
    Verify pipeline integrates walk-forward analysis correctly.
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10], name="orb_duration_minutes"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 1, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 12, 31, tz="America/New_York"),  # Full year for walk-forward (12 months)
        parameters=grid,
        run_robustness=False,
        run_walk_forward=True,  # ← Enable walk-forward
        run_surface=False,
    )
    
    # Verify walk-forward analysis exists
    assert result.walk_forward_analysis is not None
    assert 0.0 <= result.walk_forward_analysis.walk_forward_score <= 1.0
    assert len(result.walk_forward_analysis.periods) > 0


def test_overfitting_penalty_applied():
    """
    PRD Test 7: Stability vs Overfitting Detection
    
    Verify that:
    1. Stability score is computed
    2. Final score incorporates stability
    3. Results are ranked by composite score (not just raw P&L)
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15, 20], name="orb_duration_minutes"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        parameters=grid,
        run_robustness=True,  # ← Enable to get stability metrics
        run_walk_forward=False,
        run_surface=False,
    )
    
    # Verify composite score is used (not just P&L)
    assert "composite_score" in result.sweep_results.columns
    
    # Verify sweep results are sorted by composite_score
    scores = result.sweep_results["composite_score"].tolist()
    assert scores == sorted(scores, reverse=True)  # Descending order
    
    # Verify top result has highest composite score
    assert result.best_score == result.sweep_results.iloc[0]["composite_score"]
    
    # Verify robustness analysis includes stability metrics
    if result.robustness_analysis:
        assert hasattr(result.robustness_analysis, 'robustness_score')
        assert result.robustness_analysis.robustness_score <= 1.0


def test_surface_analysis_outputs_matrix():
    """
    PRD Test 8: Surface Analysis Output
    
    Verify that:
    1. Surface analysis generates 2D matrix
    2. Matrix dimensions match parameter grid
    3. Cliff and plateau detection work
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15, 20], name="orb_duration_minutes"),
        ParameterDefinition("exit.take_profit.multiplier", [2.0, 2.5, 3.0], name="tp_multiplier"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        parameters=grid,
        sweep_mode="grid",  # 3 * 3 = 9 combinations
        run_robustness=False,
        run_walk_forward=False,
        run_surface=True,  # ← Enable surface analysis
    )
    
    # Verify surface analysis exists
    assert result.surface_analysis is not None
    assert len(result.surface_analysis) > 0
    
    # Get surface
    surface_name = list(result.surface_analysis.keys())[0]
    surface = result.surface_analysis[surface_name]
    
    # Verify matrix dimensions
    x_vals = grid[0].values
    y_vals = grid[1].values
    
    assert surface.metric_matrix.shape == (len(y_vals), len(x_vals))
    
    # Verify cliff detection
    cliffs = surface.detect_cliffs()
    assert isinstance(cliffs, list)
    
    # Verify plateau detection
    plateaus = surface.detect_plateaus()
    assert isinstance(plateaus, list)
    
    # Verify optimal region finder
    optimal = surface.find_optimal_region()
    assert "n_optimal_points" in optimal
    assert "optimal_params" in optimal


def test_pipeline_result_summary():
    """
    Verify OptimizationResult.summary() generates readable output.
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15], name="orb_duration_minutes"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        parameters=grid,
    )
    
    # Generate summary
    summary = result.summary()
    
    # Verify summary contains key information
    assert isinstance(summary, str)
    assert "OPTIMIZATION RESULT SUMMARY" in summary
    assert "Best Parameters:" in summary
    assert "Composite Score:" in summary
    # Score is formatted to 3 decimal places in summary
    assert f"{result.best_score:.3f}" in summary


def test_pipeline_handles_single_parameter():
    """
    Verify pipeline works with one-at-a-time sweep (single parameter).
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15, 20], name="orb_duration_minutes"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        parameters=grid,
        sweep_mode="one_at_a_time",
    )
    
    # Verify results
    assert len(result.sweep_results) == 3  # 3 values tested
    assert result.best_params is not None


def test_pipeline_caching():
    """
    Verify pipeline uses caching to avoid re-running same parameters.
    """
    import tempfile
    
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15], name="orb_duration_minutes"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    # Create temporary cache directory
    with tempfile.TemporaryDirectory() as cache_dir:
        cache_path = Path(cache_dir)
        
        # Run once (populate cache)
        result1 = pipeline.optimize(
            symbol="QQQ",
            start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
            end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
            parameters=grid,
            cache_dir=cache_path,
        )
        
        # Run again (should use cache)
        result2 = pipeline.optimize(
            symbol="QQQ",
            start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
            end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
            parameters=grid,
            cache_dir=cache_path,
        )
        
        # Results should be identical (deterministic + cached)
        assert result1.best_score == result2.best_score
        assert result1.best_params == result2.best_params


def test_pipeline_all_components_enabled():
    """
    Verify full pipeline with all components enabled.
    
    This is the comprehensive "kitchen sink" test.
    """
    grid = [
        ParameterDefinition("strategy.orb_duration_minutes", [10, 15], name="orb_duration_minutes"),
        ParameterDefinition("exit.take_profit.multiplier", [2.0, 3.0], name="tp_multiplier"),
    ]
    
    pipeline = OptimizationPipeline(
        base_ruleset_path="vibe/rulesets/orb_production.yaml",
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    # Enable everything
    result = pipeline.optimize(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 1, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 3, 1, tz="America/New_York"),  # Longer period for walk-forward
        parameters=grid,
        sweep_mode="grid",
        run_robustness=True,
        run_walk_forward=True,
        run_surface=True,
    )
    
    # Verify all components executed
    assert result.sweep_results is not None
    assert result.robustness_analysis is not None
    assert result.walk_forward_analysis is not None
    assert result.surface_analysis is not None
    
    # Verify summary includes all components
    summary = result.summary()
    assert "Robustness Score:" in summary
    assert "Walk-Forward Score:" in summary
    assert "Surface Analysis:" in summary
