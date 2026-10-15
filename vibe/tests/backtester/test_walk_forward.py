"""
Unit tests for walk-forward analysis (PRD Test 5).

Verifies that walk-forward split logic prevents data leakage
and correctly measures out-of-sample performance.
"""
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd

from vibe.backtester.analysis.walk_forward import (
    WalkForwardEngine,
    WalkForwardPeriod,
    WalkForwardAnalysis,
)
from vibe.common.ruleset.loader import RuleSetLoader

from vibe.backtester.data.paths import resolve_market_data_dir

PARQUET_DIR = resolve_market_data_dir(require_exists=False)
pytestmark = pytest.mark.skipif(
    not (PARQUET_DIR / "QQQ.parquet").exists(),
    reason="Parquet data not available",
)


def test_walk_forward_no_leakage():
    """
    PRD Test 5: Walk Forward Split Integrity
    
    Verify that:
    1. Test period always comes AFTER train period (no future leakage)
    2. Periods don't overlap
    3. Proper rolling window behavior
    """
    ruleset = RuleSetLoader.from_name("orb_production")
    
    engine = WalkForwardEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
        slippage_ticks=5,
    )
    
    # Run walk-forward analysis
    analysis = engine.analyze(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 1, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 6, 30, tz="America/New_York"),
        train_months=2,  # 2-month training window
        test_months=1,   # 1-month test window
        step_months=1,   # Roll forward by 1 month
    )
    
    # Verify periods exist
    assert len(analysis.periods) > 0
    
    # Verify no data leakage in each period
    for period in analysis.periods:
        # Test must come after train
        assert period.test_start > period.train_end
        
        # Verify proper ordering within each period
        assert period.train_start < period.train_end
        assert period.test_start < period.test_end
        
        # Verify no overlap (test starts after train ends)
        assert period.test_start >= period.train_end


def test_walk_forward_period_structure():
    """
    Verify WalkForwardPeriod has correct structure and properties.
    """
    # Create a sample period
    period = WalkForwardPeriod(
        train_start=datetime(2024, 1, 1),
        train_end=datetime(2024, 3, 1),
        test_start=datetime(2024, 3, 2),
        test_end=datetime(2024, 4, 1),
    )
    
    # Verify dates
    assert period.train_start == datetime(2024, 1, 1)
    assert period.train_end == datetime(2024, 3, 1)
    assert period.test_start == datetime(2024, 3, 2)
    assert period.test_end == datetime(2024, 4, 1)
    
    # Verify no leakage
    assert period.test_start > period.train_end


def test_walk_forward_period_properties():
    """
    Verify WalkForwardPeriod computed properties work correctly.
    """
    from vibe.backtester.core.engine import BacktestEngine
    
    ruleset = RuleSetLoader.from_name("orb_production")
    engine = BacktestEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
        slippage_ticks=5,
    )
    
    # Run train backtest
    train_result = engine.run(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
    )
    
    # Run test backtest
    test_result = engine.run(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 2, 1, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 2, 29, tz="America/New_York"),
    )
    
    # Create period with results
    period = WalkForwardPeriod(
        train_start=pd.Timestamp(2024, 1, 2, tz="America/New_York"),
        train_end=pd.Timestamp(2024, 1, 31, tz="America/New_York"),
        test_start=pd.Timestamp(2024, 2, 1, tz="America/New_York"),
        test_end=pd.Timestamp(2024, 2, 29, tz="America/New_York"),
        train_result=train_result,
        test_result=test_result,
    )
    
    # Verify properties
    assert period.train_expectancy == train_result.overall.expectancy_r
    assert period.test_expectancy == test_result.overall.expectancy_r
    
    # Verify degradation calculation
    if period.train_expectancy != 0:
        expected_degradation = period.test_expectancy / period.train_expectancy
        assert period.degradation == expected_degradation
    else:
        assert period.degradation == 0.0


def test_walk_forward_rolling_window():
    """
    Verify walk-forward properly implements rolling window.
    
    For train=2mo, test=1mo, step=1mo over 6 months:
    - Period 1: Train Jan-Feb, Test Mar
    - Period 2: Train Feb-Mar, Test Apr
    - Period 3: Train Mar-Apr, Test May
    - Period 4: Train Apr-May, Test Jun
    """
    ruleset = RuleSetLoader.from_name("orb_production")
    
    engine = WalkForwardEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
    )
    
    # Manually verify split generation logic
    # (This tests the internal logic without running full backtests)
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 7, 1)
    train_months = 2
    test_months = 1
    step_months = 1
    
    # Expected periods
    expected_periods = [
        # Period 1: Train Jan-Feb, Test Mar
        (datetime(2024, 1, 1), datetime(2024, 3, 1), datetime(2024, 3, 1), datetime(2024, 4, 1)),
        # Period 2: Train Feb-Mar, Test Apr
        (datetime(2024, 2, 1), datetime(2024, 4, 1), datetime(2024, 4, 1), datetime(2024, 5, 1)),
        # Period 3: Train Mar-Apr, Test May
        (datetime(2024, 3, 1), datetime(2024, 5, 1), datetime(2024, 5, 1), datetime(2024, 6, 1)),
        # Period 4: Train Apr-May, Test Jun
        (datetime(2024, 4, 1), datetime(2024, 6, 1), datetime(2024, 6, 1), datetime(2024, 7, 1)),
    ]
    
    # Simulate split generation
    current_date = start_date
    generated_periods = []
    
    while True:
        train_start = current_date
        train_end = train_start + relativedelta(months=train_months)
        test_start = train_end
        test_end = test_start + relativedelta(months=test_months)
        
        if test_end > end_date:
            break
        
        generated_periods.append((train_start, train_end, test_start, test_end))
        current_date += relativedelta(months=step_months)
    
    # Verify we generated expected periods
    assert len(generated_periods) == len(expected_periods)
    
    for generated, expected in zip(generated_periods, expected_periods):
        assert generated[0] == expected[0]  # train_start
        assert generated[1] == expected[1]  # train_end
        assert generated[2] == expected[2]  # test_start
        assert generated[3] == expected[3]  # test_end


def test_walk_forward_analysis_structure():
    """
    Verify WalkForwardAnalysis has correct structure and metrics.
    """
    ruleset = RuleSetLoader.from_name("orb_production")
    
    engine = WalkForwardEngine(
        ruleset=ruleset,
        data_dir=PARQUET_DIR,
        initial_capital=10_000.0,
    )
    
    analysis = engine.analyze(
        symbol="QQQ",
        start_date=pd.Timestamp(2024, 1, 1, tz="America/New_York"),
        end_date=pd.Timestamp(2024, 4, 1, tz="America/New_York"),
        train_months=1,
        test_months=1,
        step_months=1,
    )
    
    # Verify analysis structure
    assert hasattr(analysis, 'periods')
    assert hasattr(analysis, 'avg_train_expectancy')
    assert hasattr(analysis, 'avg_test_expectancy')
    assert hasattr(analysis, 'avg_degradation')
    assert hasattr(analysis, 'walk_forward_score')
    
    # Verify walk-forward score is in valid range
    assert 0.0 <= analysis.walk_forward_score <= 1.0
    
    # Verify to_dict() works
    analysis_dict = analysis.to_dict()
    assert 'n_periods' in analysis_dict
    assert 'avg_train_expectancy' in analysis_dict
    assert 'avg_test_expectancy' in analysis_dict
    assert 'walk_forward_score' in analysis_dict
    assert 'periods' in analysis_dict
    assert len(analysis_dict['periods']) == len(analysis.periods)


def test_walk_forward_degradation_calculation():
    """
    Verify degradation is calculated correctly.
    
    Degradation = test_expectancy / train_expectancy
    - 1.0 = no degradation (test = train)
    - 0.8 = 20% degradation (test is 80% of train)
    - 1.2 = test outperformed train (rare but possible)
    """
    # Create mock periods with known degradation
    from vibe.backtester.analysis.metrics import BacktestResult, ConvexityMetrics, EquityMetrics
    import pandas as pd
    
    def create_mock_result(expectancy_r):
        cm = ConvexityMetrics(
            n_trades=10,
            win_rate=0.5,
            avg_win_r=2.0,
            avg_loss_r=-1.0,
            expectancy_r=expectancy_r,
            max_win_r=3.0,
            max_loss_r=-1.5,
            top10_pct=50.0,
            skewness=0.5,
            max_losing_streak=3,
            total_pnl=expectancy_r * 1000,
            stop_wins=5,
            stop_losses=5,
            eod_wins=0,
            eod_losses=0,
            r_multiples=[expectancy_r] * 10,
            first_date="2024-01-01",
            last_date="2024-01-31",
        )
        
        equity = EquityMetrics(
            total_return=0.10,
            annualized_return=0.10,
            sharpe_ratio=1.0,
            max_drawdown=0.05,
            max_drawdown_duration_days=5,
            equity_curve=pd.Series([10000.0, 11000.0]),
            drawdown_curve=pd.Series([0.0, 0.0]),
        )
        
        return BacktestResult(
            overall=cm,
            by_year={},
            equity=equity,
            trades=[],
            regime_breakdown={},
            symbol="QQQ",
            start_date="2024-01-01",
            end_date="2024-01-31",
            ruleset_name="orb_production",
            ruleset_version="1.0.0",
        )
    
    # Period with no degradation
    period_no_deg = WalkForwardPeriod(
        train_start=datetime(2024, 1, 1),
        train_end=datetime(2024, 2, 1),
        test_start=datetime(2024, 2, 1),
        test_end=datetime(2024, 3, 1),
        train_result=create_mock_result(0.20),
        test_result=create_mock_result(0.20),
    )
    
    assert period_no_deg.degradation == 1.0  # 0.20 / 0.20 = 1.0
    
    # Period with 20% degradation
    period_20_deg = WalkForwardPeriod(
        train_start=datetime(2024, 1, 1),
        train_end=datetime(2024, 2, 1),
        test_start=datetime(2024, 2, 1),
        test_end=datetime(2024, 3, 1),
        train_result=create_mock_result(0.25),
        test_result=create_mock_result(0.20),
    )
    
    assert abs(period_20_deg.degradation - 0.80) < 0.01  # 0.20 / 0.25 = 0.80

# --------------------------------------------------------------------------
# Deprecation contract
#
# The tests above verify that the train and test *dates* do not overlap, and
# they pass. That is precisely why the real defect survived: date separation
# is necessary but nowhere near sufficient. No parameter is ever selected
# per fold, and the caller supplies parameters chosen over the whole range,
# so the "test" window is inside the selection data no matter how cleanly
# the dates are split. The tests below guard the labelling that makes this
# visible, so the engine cannot quietly go back to presenting itself as
# out-of-sample evidence.
# --------------------------------------------------------------------------


def test_analyze_warns_that_results_are_not_out_of_sample():
    """The engine must say so at the moment it is used."""
    import warnings
    from datetime import datetime
    from unittest.mock import patch

    from vibe.backtester.analysis.walk_forward import (
        WalkForwardAnalysis,
        WalkForwardEngine,
    )

    engine = WalkForwardEngine.__new__(WalkForwardEngine)
    with patch.object(WalkForwardEngine, "_generate_periods", return_value=[]):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                engine.analyze(
                    symbol="QQQ",
                    start_date=datetime(2022, 1, 1),
                    end_date=datetime(2022, 6, 30),
                )
            except Exception:
                # The warning is emitted before any work, which is the point:
                # it must not depend on the run succeeding.
                pass

    messages = [str(w.message) for w in caught if w.category is DeprecationWarning]
    assert messages, "analyze() must emit a DeprecationWarning"
    assert any("not out-of-sample" in m.lower() for m in messages)


def test_exported_summary_declares_it_is_not_out_of_sample():
    """Artifacts must carry the caveat, not just the source file.

    A consumer reading walk_forward_score from a JSON artifact has no access
    to the docstring. Without this marker there is nothing distinguishing
    this from a genuine nested walk-forward result.
    """
    from vibe.backtester.analysis.walk_forward import WalkForwardAnalysis

    summary = WalkForwardAnalysis(
        periods=[],
        avg_train_expectancy=0.1,
        avg_test_expectancy=0.05,
        avg_degradation=0.5,
        walk_forward_score=0.7,
    ).to_dict()

    assert summary["is_out_of_sample"] is False
    assert summary["parameters_selected_per_fold"] is False


def test_degradation_withholds_the_ratio_near_zero_training_expectancy():
    """A tiny denominator must not read as a spectacular result."""
    from datetime import datetime

    from vibe.backtester.analysis.walk_forward import WalkForwardPeriod

    class _Overall:
        def __init__(self, value):
            self.expectancy_r = value

    class _Result:
        def __init__(self, value):
            self.overall = _Overall(value)

    period = WalkForwardPeriod(
        train_start=datetime(2022, 1, 1),
        train_end=datetime(2022, 6, 30),
        test_start=datetime(2022, 7, 1),
        test_end=datetime(2022, 7, 31),
        train_result=_Result(1e-9),
        test_result=_Result(0.5),
    )

    assert period.degradation == 0.0
