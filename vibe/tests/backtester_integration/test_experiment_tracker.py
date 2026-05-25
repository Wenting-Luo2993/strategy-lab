"""
Tests for Backtester Integration with Research Journal
"""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from vibe.backtester.integration.experiment_tracker import (
    BacktestExperimentTracker,
    wrap_backtest_engine,
)
from vibe.backtester.analysis.metrics import BacktestResult
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.models import ExperimentStatus


class TestBacktestExperimentTracker:
    """Test BacktestExperimentTracker."""
    
    def test_tracker_disabled_without_registry(self):
        """Tracker should be disabled when registry is None."""
        tracker = BacktestExperimentTracker(registry=None)
        assert not tracker.can_track()
        assert tracker.enabled is False
    
    def test_tracker_enabled_with_registry(self, tmp_path):
        """Tracker should be enabled with registry."""
        registry = ResearchRegistry(tmp_path)
        tracker = BacktestExperimentTracker(registry=registry)
        assert tracker.can_track()
        assert tracker.enabled is True
    
    def test_track_backtest_result_returns_none_when_disabled(self):
        """track_backtest_result should return None when tracker disabled."""
        tracker = BacktestExperimentTracker(registry=None)
        result = tracker.track_backtest_result(
            backtest_result=MagicMock(),
            experiment_id="EXP-001",
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
            conclusion="Test"
        )
        assert result is None
    
    def test_track_backtest_result_with_registry(self, tmp_path):
        """track_backtest_result should create and complete experiment."""
        registry = ResearchRegistry(tmp_path)
        
        # Create experiment first
        exp = registry.create_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
        )
        
        # Create mock backtest result with trades
        mock_result = MagicMock()
        mock_result.trades = []
        
        # Track result
        tracker = BacktestExperimentTracker(registry=registry)
        result_id = tracker.track_backtest_result(
            backtest_result=mock_result,
            experiment_id=exp.id,
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
            conclusion="Test completed"
        )
        
        assert result_id == exp.id
        
        # Verify experiment completed
        completed = registry.get_experiment(exp.id)
        assert completed.status == ExperimentStatus.COMPLETED


class TestWrapBacktestEngine:
    """Test wrap_backtest_engine function."""
    
    def test_wrap_engine_without_registry(self):
        """Wrapped engine should work without registry."""
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_engine.run.return_value = mock_result
        
        wrapped = wrap_backtest_engine(mock_engine, registry=None)
        
        result = wrapped.run(
            symbol="QQQ",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31),
            experiment_id="EXP-001"
        )
        
        # Should call original run()
        mock_engine.run.assert_called_once()
        assert result == mock_result
    
    def test_wrap_engine_adds_experiment_tracking(self, tmp_path):
        """Wrapped engine should track experiment when provided."""
        registry = ResearchRegistry(tmp_path)
        
        # Create experiment
        exp = registry.create_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
        )
        
        # Create mock engine
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.trades = []
        mock_engine.run.return_value = mock_result
        mock_engine.ruleset.strategy_name = "TestStrategy"
        mock_engine.ruleset.version = "1.0"
        mock_engine.ruleset.parameters = {}
        
        # Wrap engine
        wrapped = wrap_backtest_engine(mock_engine, registry=registry)
        
        # Run with experiment_id
        result = wrapped.run(
            symbol="QQQ",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31),
            experiment_id=exp.id
        )
        
        # Verify original run() called
        mock_engine.run.assert_called_once()
        assert result == mock_result
        
        # Verify experiment completed
        completed = registry.get_experiment(exp.id)
        assert completed.status == ExperimentStatus.COMPLETED
    
    def test_wrap_engine_without_experiment_id(self):
        """Wrapped engine should work without experiment_id."""
        registry = MagicMock()
        
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.trades = []
        mock_engine.run.return_value = mock_result
        
        wrapped = wrap_backtest_engine(mock_engine, registry=registry)
        
        # Run WITHOUT experiment_id
        result = wrapped.run(
            symbol="QQQ",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31)
        )
        
        # Should call original run()
        mock_engine.run.assert_called_once()
        assert result == mock_result
    
    def test_wrap_engine_preserves_precomputed_features(self):
        """Wrapped engine should pass through precomputed_features."""
        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_result.trades = []
        mock_engine.run.return_value = mock_result
        
        wrapped = wrap_backtest_engine(mock_engine, registry=None)
        
        # Mock features
        mock_features = {"test": "feature"}
        
        result = wrapped.run(
            symbol="QQQ",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 12, 31),
            precomputed_features=mock_features
        )
        
        # Verify precomputed_features passed to original
        call_args = mock_engine.run.call_args
        assert call_args[1]["precomputed_features"] == mock_features


class TestBacktestTrackerWithRealRegistry:
    """Integration tests with real ResearchRegistry."""
    
    def test_end_to_end_backtest_tracking(self, tmp_path):
        """Test complete flow: create hypothesis → experiment → track backtest."""
        registry = ResearchRegistry(tmp_path)
        
        # 1. Create hypothesis
        hyp = registry.create_hypothesis(
            title="Test strategy",
            rationale="Testing framework",
            tags=["test"]
        )
        
        # 2. Create experiment
        exp = registry.create_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={"param1": 10},
            dataset_config={"symbol": "TEST"},
            hypothesis_id=hyp.id
        )
        
        assert exp.status == ExperimentStatus.REGISTERED
        
        # 3. Mock and track backtest
        mock_result = MagicMock()
        mock_result.trades = []
        
        tracker = BacktestExperimentTracker(registry=registry)
        result_id = tracker.track_backtest_result(
            backtest_result=mock_result,
            experiment_id=exp.id,
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={"param1": 10},
            dataset_config={"symbol": "TEST"},
            conclusion="Test completed successfully"
        )
        
        # 4. Verify completion
        completed = registry.get_experiment(exp.id)
        assert completed.status == ExperimentStatus.COMPLETED
        assert completed.conclusion is not None
        assert "Test completed" in completed.conclusion
    
    def test_query_completed_experiments_after_tracking(self, tmp_path):
        """Query should find tracked experiments."""
        registry = ResearchRegistry(tmp_path)
        
        # Create and track multiple experiments
        for i in range(3):
            exp = registry.create_experiment(
                strategy_name="TestStrategy",
                strategy_version="1.0",
                parameters={"param": i},
                dataset_config={}
            )
            
            tracker = BacktestExperimentTracker(registry=registry)
            mock_result = MagicMock()
            mock_result.trades = []
            
            tracker.track_backtest_result(
                backtest_result=mock_result,
                experiment_id=exp.id,
                strategy_name="TestStrategy",
                strategy_version="1.0",
                parameters={"param": i},
                dataset_config={},
                conclusion=f"Experiment {i}"
            )
        
        # Query completed
        from vibe.research_journal.query import ExperimentQuery
        query = ExperimentQuery(registry)
        completed = query.by_status(ExperimentStatus.COMPLETED).execute()
        
        assert len(completed) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
