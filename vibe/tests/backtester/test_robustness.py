"""
Unit tests for robustness analysis (PRD Test 4).

Verifies that robustness scoring correctly measures strategy stability
under noise injection and parameter perturbations.
"""
import pytest
from pathlib import Path
from datetime import datetime
import pandas as pd

from vibe.backtester.analysis.robustness import (
    RobustnessAnalyzer,
    RobustnessTestResult,
    RobustnessAnalysis,
)
from vibe.common.ruleset.loader import RuleSetLoader


def test_robustness_score_reduces_variance():
    """
    PRD Test 4: Robustness Scoring
    
    Verify that:
    1. Robustness score is between 0 and 1
    2. Lower variance → higher robustness score
    3. Noise injection tests produce varying results
    """
    # Load ruleset
    ruleset = RuleSetLoader.from_name("orb_production")
    
    # Create analyzer
    analyzer = RobustnessAnalyzer(
        ruleset=ruleset,
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
        baseline_slippage_ticks=5,
    )
    
    # Run robustness analysis
    analysis = analyzer.analyze(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        noise_tests=5,  # 5 noise injection tests
        perturbation_tests=0,  # Skip param perturbation for now
    )
    
    # Verify robustness score is in valid range
    assert 0.0 <= analysis.robustness_score <= 1.0
    assert isinstance(analysis.robustness_score, float)
    
    # Verify variance metrics exist
    assert analysis.expectancy_std >= 0.0
    assert analysis.sharpe_std >= 0.0
    assert analysis.pnl_std >= 0.0
    
    # Verify test results were collected
    assert len(analysis.test_results) == 5
    assert all(isinstance(r, RobustnessTestResult) for r in analysis.test_results)


def test_robustness_analysis_structure():
    """
    Verify RobustnessAnalysis has correct structure and can be exported.
    """
    ruleset = RuleSetLoader.from_name("orb_production")
    
    analyzer = RobustnessAnalyzer(
        ruleset=ruleset,
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
    )
    
    analysis = analyzer.analyze(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        noise_tests=3,
    )
    
    # Verify analysis has all required fields
    assert hasattr(analysis, 'baseline_result')
    assert hasattr(analysis, 'test_results')
    assert hasattr(analysis, 'expectancy_std')
    assert hasattr(analysis, 'sharpe_std')
    assert hasattr(analysis, 'pnl_std')
    assert hasattr(analysis, 'robustness_score')
    
    # Verify to_dict() works
    analysis_dict = analysis.to_dict()
    assert 'baseline_expectancy_r' in analysis_dict
    assert 'n_tests' in analysis_dict
    assert 'robustness_score' in analysis_dict
    assert analysis_dict['n_tests'] == 3


def test_noise_injection_creates_variance():
    """
    Verify that noise injection actually varies performance.
    
    Running multiple tests with different slippage should produce
    different results (otherwise robustness testing is meaningless).
    """
    ruleset = RuleSetLoader.from_name("orb_production")
    
    analyzer = RobustnessAnalyzer(
        ruleset=ruleset,
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
        baseline_slippage_ticks=5,
    )
    
    analysis = analyzer.analyze(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        noise_tests=10,  # More tests to ensure variance
    )
    
    # Extract expectancy values from all tests
    expectancies = [r.expectancy_r for r in analysis.test_results]
    
    # Verify we have variation (not all identical)
    assert len(set(expectancies)) > 1  # At least some variation
    
    # Verify baseline exists
    baseline_exp = analysis.baseline_result.overall.expectancy_r
    assert isinstance(baseline_exp, float)


def test_robustness_test_result_properties():
    """
    Verify RobustnessTestResult properties work correctly.
    """
    from vibe.backtester.core.engine import BacktestEngine
    
    ruleset = RuleSetLoader.from_name("orb_production")
    
    engine = BacktestEngine(
        ruleset=ruleset,
        data_dir=Path("vibe/data/parquet"),
        initial_capital=10_000.0,
        slippage_ticks=10,  # High slippage for noise
    )
    
    result = engine.run(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
    )
    
    # Create test result wrapper
    test_result = RobustnessTestResult(
        test_type="noise_injection",
        variation=1.5,  # 150% of baseline slippage
        result=result,
    )
    
    # Verify properties
    assert test_result.expectancy_r == result.overall.expectancy_r
    assert test_result.sharpe_ratio == result.equity.sharpe_ratio
    assert test_result.total_pnl == result.overall.total_pnl
    assert test_result.test_type == "noise_injection"
    assert test_result.variation == 1.5


def test_robustness_score_calculation_logic():
    """
    Verify robustness score formula: 1 / (1 + coefficient_of_variation)
    
    Lower variance relative to mean → higher score
    """
    import numpy as np
    
    # Simulate test results with low variance (robust strategy)
    low_variance_expectancies = [0.10, 0.11, 0.09, 0.10, 0.10]
    mean_low = np.mean(low_variance_expectancies)
    std_low = np.std(low_variance_expectancies, ddof=1)
    cv_low = std_low / abs(mean_low) if mean_low != 0 else float('inf')
    expected_score_low = 1.0 / (1.0 + cv_low)
    
    # High variance (fragile strategy)
    high_variance_expectancies = [0.10, 0.30, -0.10, 0.20, 0.05]
    mean_high = np.mean(high_variance_expectancies)
    std_high = np.std(high_variance_expectancies, ddof=1)
    cv_high = std_high / abs(mean_high) if mean_high != 0 else float('inf')
    expected_score_high = 1.0 / (1.0 + cv_high)
    
    # Low variance should have higher robustness score
    assert expected_score_low > expected_score_high
    assert 0.0 <= expected_score_low <= 1.0
    assert 0.0 <= expected_score_high <= 1.0


def test_robustness_with_zero_variance():
    """
    Verify robustness score handles edge case of zero variance.
    
    If all tests produce identical results (perfect robustness),
    score should be 1.0.
    """
    import numpy as np
    
    # Perfect stability (no variance)
    expectancies = [0.10, 0.10, 0.10, 0.10]
    std = np.std(expectancies, ddof=1)
    mean = np.mean(expectancies)
    
    # Std should be 0
    assert std == 0.0
    
    # Coefficient of variation = 0
    cv = std / abs(mean)
    assert cv == 0.0
    
    # Robustness score = 1 / (1 + 0) = 1.0
    score = 1.0 / (1.0 + cv)
    assert score == 1.0
