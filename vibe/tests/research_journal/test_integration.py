"""
Tests for Integration Adapters (Stage 8)

P0 Tests:
  - Create experiment from backtest results
  - Link trades to experiments
  - Create child experiments for parameter variations

P1 Tests:
  - Integration workflow end-to-end
  - Metadata preservation across systems
"""

import pytest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.models import ExperimentStatus
from vibe.research_journal.integration.backtest_adapter import (
    BacktestResultAdapter,
)
from vibe.research_journal.persistence import ensure_research_directories
from vibe.common.models.trade import Trade


@pytest.fixture
def temp_research_dir():
    """Create temporary research directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "research"


@pytest.fixture
def registry(temp_research_dir):
    """Create registry with sample hypothesis."""
    ensure_research_directories(temp_research_dir)
    reg = ResearchRegistry(temp_research_dir)
    
    hyp = reg.create_hypothesis(
        title="Test backtest results",
        rationale="Testing integration with backtester",
        tags=["integration", "backtest"]
    )
    
    return reg, hyp


@pytest.fixture
def sample_trades():
    """Create sample trades for testing."""
    return [
        Trade(
            trade_id="T001",
            symbol="QQQ",
            side="buy",
            quantity=100,
            entry_price=350.0,
            exit_price=360.0,
            entry_time=datetime(2024, 1, 15, 9, 30, 0, tzinfo=timezone.utc),
            exit_time=datetime(2024, 1, 15, 10, 45, 0, tzinfo=timezone.utc),
            strategy="ORB",
            exit_reason="TARGET"
        ),
        Trade(
            trade_id="T002",
            symbol="QQQ",
            side="sell",
            quantity=100,
            entry_price=360.0,
            exit_price=355.0,
            entry_time=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
            exit_time=datetime(2024, 1, 15, 15, 30, 0, tzinfo=timezone.utc),
            strategy="ORB",
            exit_reason="EOD"
        ),
    ]


class TestBacktestResultAdapter:
    """P0: Backtest result integration tests."""
    
    def test_create_experiment_from_trades(self, registry, sample_trades):
        """Test creating experiment from backtest trades."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        exp = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={
                "orb_minutes": 5,
                "take_profit": 2.0,
                "stop_loss": 1.0
            },
            dataset_config={
                "symbols": ["QQQ"],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31"
            },
            trades=sample_trades,
            tags=["backtest", "validation"]
        )
        
        assert exp.id.startswith("EXP-")
        assert exp.hypothesis_id == hyp.id
        assert exp.strategy_name == "ORBStrategy"
        assert exp.strategy_version == "1.4.2"
        assert len(exp.tags) >= 2  # At least "backtest" and "validation"
    
    def test_compute_metrics_from_trades(self, registry, sample_trades):
        """Test computing backtest metrics from trades."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        exp = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={},
            dataset_config={},
            trades=sample_trades
        )
        
        # Metrics should be computed
        assert exp.results_summary is not None
        assert "total_trades" in exp.results_summary
        assert "win_rate" in exp.results_summary
        assert "total_pnl" in exp.results_summary
        assert exp.results_summary["total_trades"] == 2
    
    def test_backtest_metrics_accuracy(self, registry, sample_trades):
        """Test that computed metrics are accurate."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        exp = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={},
            dataset_config={},
            trades=sample_trades
        )
        
        # Trade 1: (360 - 350) * 100 = 1000 (win)
        # Trade 2: (360 - 355) * 100 = 500 (win)
        # Total: 1500
        assert exp.results_summary["total_pnl"] == 1500.0
        assert exp.results_summary["win_rate"] == 1.0  # 2/2 = 100%
        assert exp.results_summary["winning_trades"] == 2
        assert exp.results_summary["losing_trades"] == 0


class TestExperimentCompletion:
    """P0: Experiment completion workflow tests."""
    
    def test_complete_experiment_with_backtest_results(self, registry, sample_trades):
        """Test completing experiment with backtest results."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        exp = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={},
            dataset_config={},
            trades=sample_trades
        )
        
        # Complete experiment with results
        completed_exp = adapter.complete_experiment(
            exp.id,
            trades=sample_trades,
            conclusion="Backtest successful with positive expectancy."
        )
        
        assert completed_exp.status == ExperimentStatus.COMPLETED
        assert completed_exp.conclusion is not None
        assert "positive expectancy" in completed_exp.conclusion
    
    def test_experiment_immutability_after_completion(self, registry, sample_trades):
        """Test that experiment becomes immutable after completion."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        exp = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={},
            dataset_config={},
            trades=sample_trades
        )
        
        completed_exp = adapter.complete_experiment(
            exp.id,
            trades=sample_trades,
            conclusion="Test conclusion"
        )
        
        # Verify immutability
        assert completed_exp.is_immutable()


class TestParameterVariationTracking:
    """P1: Parameter variation and lineage tests."""
    
    def test_create_child_experiment_with_parameters(self, registry, sample_trades):
        """Test creating child experiment for parameter variation."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        
        # Parent experiment
        parent = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"orb_minutes": 5, "take_profit": 2.0},
            dataset_config={"symbols": ["QQQ"]},
            trades=sample_trades,
            tags=["parent"]
        )
        
        # Child experiment with different parameters
        child = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"orb_minutes": 10, "take_profit": 3.0},
            dataset_config={"symbols": ["QQQ"]},
            parent_experiment_id=parent.id,
            trades=sample_trades,
            tags=["child"]
        )
        
        assert child.parent_experiment_id == parent.id
        assert child.parameters["orb_minutes"] == 10
        assert parent.parameters["orb_minutes"] == 5
    
    def test_lineage_tracking_for_optimization(self, registry, sample_trades):
        """Test lineage tracking across optimization iterations."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        
        # Create optimization chain
        exp1 = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"param_a": 1},
            dataset_config={},
            trades=sample_trades,
            tags=["iteration_1"]
        )
        
        exp2 = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"param_a": 2},
            dataset_config={},
            parent_experiment_id=exp1.id,
            trades=sample_trades,
            tags=["iteration_2"]
        )
        
        exp3 = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"param_a": 3},
            dataset_config={},
            parent_experiment_id=exp2.id,
            trades=sample_trades,
            tags=["iteration_3"]
        )
        
        # Verify lineage
        assert exp2.parent_experiment_id == exp1.id
        assert exp3.parent_experiment_id == exp2.id
        
        # Verify no cycles
        lineage = reg.get_lineage_graph()
        assert lineage.find_root(exp3.id) == exp1.id


class TestIntegrationWorkflow:
    """P1: End-to-end integration workflow tests."""
    
    def test_full_backtest_to_experiment_workflow(self, registry, sample_trades):
        """Test complete workflow from backtest to experiment."""
        reg, hyp = registry
        
        adapter = BacktestResultAdapter(reg)
        
        # Create experiment
        exp = adapter.create_experiment_from_trades(
            hypothesis_id=hyp.id,
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"orb_minutes": 5},
            dataset_config={"symbols": ["QQQ"]},
            trades=sample_trades,
            tags=["backtest"]
        )
        
        # Add research note
        note = reg.add_research_note(
            content="Backtest results show consistent performance across all test periods.",
            related_experiment_id=exp.id,
            tags=["backtest", "observations"]
        )
        
        # Complete experiment
        completed = adapter.complete_experiment(
            exp.id,
            trades=sample_trades,
            conclusion="Strategy is ready for paper trading validation."
        )
        
        # Verify workflow
        assert completed.status == ExperimentStatus.COMPLETED
        assert note.related_experiment_id == exp.id
        assert completed.conclusion == "Strategy is ready for paper trading validation."
        
        # Verify persistence
        loaded_exp = reg.get_experiment(exp.id)
        assert loaded_exp.status == ExperimentStatus.COMPLETED
