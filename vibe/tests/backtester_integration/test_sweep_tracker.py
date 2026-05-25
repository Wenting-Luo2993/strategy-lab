"""
Tests for Parameter Sweep Integration with Research Journal
"""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
import pandas as pd
from vibe.backtester.integration.sweep_tracker import (
    ParameterSweepExperimentTracker,
    SweepResultExperimentLinker,
)
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.models import ExperimentStatus


class TestParameterSweepExperimentTracker:
    """Test ParameterSweepExperimentTracker."""
    
    def test_tracker_disabled_without_registry(self):
        """Tracker should be disabled when registry is None."""
        tracker = ParameterSweepExperimentTracker(registry=None)
        assert not tracker.enabled
    
    def test_tracker_enabled_with_registry(self, tmp_path):
        """Tracker should be enabled with registry."""
        registry = ResearchRegistry(tmp_path)
        tracker = ParameterSweepExperimentTracker(registry=registry)
        assert tracker.enabled is True
    
    def test_create_parent_experiment_returns_none_when_disabled(self):
        """create_parent_experiment should return None when disabled."""
        tracker = ParameterSweepExperimentTracker(registry=None)
        result = tracker.create_parent_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            base_parameters={},
            dataset_config={}
        )
        assert result is None
    
    def test_create_parent_experiment(self, tmp_path):
        """Should create parent experiment for sweep."""
        registry = ResearchRegistry(tmp_path)
        tracker = ParameterSweepExperimentTracker(registry=registry)
        
        parent_id = tracker.create_parent_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            base_parameters={"param1": 5, "param2": 2.0},
            dataset_config={"symbol": "QQQ"},
            tags=["sweep"]
        )
        
        assert parent_id is not None
        assert parent_id.startswith("EXP-")
        
        # Verify parent exists
        parent = registry.get_experiment(parent_id)
        assert parent.strategy_name == "TestStrategy"
        assert "sweep" in parent.tags
    
    def test_create_variation_experiment_returns_none_when_disabled(self):
        """create_variation_experiment should return None when disabled."""
        tracker = ParameterSweepExperimentTracker(registry=None)
        result = tracker.create_variation_experiment(
            variation_number=1,
            strategy_name="TestStrategy",
            strategy_version="1.0",
            variation_parameters={},
            dataset_config={}
        )
        assert result is None
    
    def test_create_variation_experiment(self, tmp_path):
        """Should create child experiment linked to parent."""
        registry = ResearchRegistry(tmp_path)
        tracker = ParameterSweepExperimentTracker(registry=registry)
        
        # Create parent
        parent_id = tracker.create_parent_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            base_parameters={},
            dataset_config={}
        )
        
        # Create variation
        var_id = tracker.create_variation_experiment(
            variation_number=1,
            strategy_name="TestStrategy",
            strategy_version="1.0",
            variation_parameters={"param1": 10},
            dataset_config={"symbol": "QQQ"}
        )
        
        assert var_id is not None
        
        # Verify parent-child relationship
        var = registry.get_experiment(var_id)
        assert var.parent_experiment_id == parent_id
    
    def test_create_multiple_variations(self, tmp_path):
        """Should create multiple child experiments."""
        registry = ResearchRegistry(tmp_path)
        tracker = ParameterSweepExperimentTracker(registry=registry)
        
        parent_id = tracker.create_parent_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            base_parameters={},
            dataset_config={}
        )
        
        # Create 3 variations
        var_ids = []
        for i in range(3):
            var_id = tracker.create_variation_experiment(
                variation_number=i + 1,
                strategy_name="TestStrategy",
                strategy_version="1.0",
                variation_parameters={"param": i * 5},
                dataset_config={}
            )
            var_ids.append(var_id)
        
        # Verify all linked to parent
        lineage = registry.get_lineage_graph()
        children = lineage.get_children(parent_id)
        assert len(children) == 3
        assert all(var_id in children for var_id in var_ids)
    
    def test_complete_variation_experiment(self, tmp_path):
        """Should complete variation with metrics."""
        registry = ResearchRegistry(tmp_path)
        tracker = ParameterSweepExperimentTracker(registry=registry)
        
        parent_id = tracker.create_parent_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            base_parameters={},
            dataset_config={}
        )
        
        var_id = tracker.create_variation_experiment(
            variation_number=1,
            strategy_name="TestStrategy",
            strategy_version="1.0",
            variation_parameters={"param": 10},
            dataset_config={}
        )
        
        # Complete
        completed_id = tracker.complete_variation_experiment(
            experiment_id=var_id,
            trades=[],
            metrics={"sharpe": 1.5, "expectancy": 0.05},
            rank=1,
            conclusion="Best result"
        )
        
        assert completed_id == var_id
        
        # Verify completion
        completed = registry.get_experiment(var_id)
        assert completed.status == ExperimentStatus.COMPLETED
        assert completed.results_summary["sharpe"] == 1.5


class TestSweepResultExperimentLinker:
    """Test SweepResultExperimentLinker."""
    
    def test_linker_disabled_without_registry(self):
        """Linker should be disabled when registry is None."""
        linker = SweepResultExperimentLinker(registry=None)
        assert not linker.enabled
    
    def test_linker_enabled_with_registry(self, tmp_path):
        """Linker should be enabled with registry."""
        registry = ResearchRegistry(tmp_path)
        linker = SweepResultExperimentLinker(registry=registry)
        assert linker.enabled is True
    
    def test_link_sweep_results_returns_when_disabled(self):
        """link_sweep_results should return early when disabled."""
        linker = SweepResultExperimentLinker(registry=None)
        
        # Should not raise
        linker.link_sweep_results(
            parent_experiment_id="EXP-001",
            sweep_results_df=pd.DataFrame()
        )
    
    def test_link_sweep_results_creates_note(self, tmp_path):
        """Should create research note for sweep results."""
        registry = ResearchRegistry(tmp_path)
        
        # Create parent experiment
        parent = registry.create_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            parameters={},
            dataset_config={}
        )
        
        # Create results DataFrame
        results_df = pd.DataFrame({
            "score": [1.5, 1.2, 0.8],
            "sharpe": [1.5, 1.2, 0.8],
            "rank": [1, 2, 3]
        })
        
        # Link results
        linker = SweepResultExperimentLinker(registry=registry)
        linker.link_sweep_results(
            parent_experiment_id=parent.id,
            sweep_results_df=results_df
        )
        
        # Verify note created
        notes = [n for n in Path(tmp_path, "research/notes").glob("*.md") 
                 if n.exists()]
        assert len(notes) > 0


class TestParameterSweepEndToEnd:
    """End-to-end tests for sweep tracking."""
    
    def test_sweep_with_parent_and_children(self, tmp_path):
        """Test complete sweep: parent + 9 child variations."""
        registry = ResearchRegistry(tmp_path)
        
        # Create hypothesis
        hyp = registry.create_hypothesis(
            title="ORB optimization",
            rationale="Find best parameters",
            tags=["optimization"]
        )
        
        # Setup tracker
        tracker = ParameterSweepExperimentTracker(
            registry=registry,
            hypothesis_id=hyp.id
        )
        
        # Create parent
        parent_id = tracker.create_parent_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            base_parameters={"orb_minutes": 5, "tp": 2.0},
            dataset_config={"symbol": "QQQ"},
            tags=["sweep_optimization"]
        )
        
        # Create 9 variations (3x3 grid: ORB × TP)
        var_metrics = []
        rank = 1
        
        for orb_min in [5, 10, 15]:
            for tp in [1.5, 2.0, 2.5]:
                var_id = tracker.create_variation_experiment(
                    variation_number=rank,
                    strategy_name="ORBStrategy",
                    strategy_version="1.4.2",
                    variation_parameters={"orb_minutes": orb_min, "tp": tp},
                    dataset_config={"symbol": "QQQ"}
                )
                
                # Complete with metrics (best to worst)
                sharpe = 2.0 - (rank - 1) * 0.1
                
                tracker.complete_variation_experiment(
                    experiment_id=var_id,
                    trades=[],
                    metrics={
                        "sharpe": sharpe,
                        "expectancy": 0.05 - (rank - 1) * 0.001
                    },
                    rank=rank,
                    conclusion=f"Rank {rank}: ORB {orb_min}m, TP {tp}"
                )
                
                var_metrics.append((var_id, sharpe, rank))
                rank += 1
        
        # Verify lineage
        lineage = registry.get_lineage_graph()
        children = lineage.get_children(parent_id)
        assert len(children) == 9
        
        # Verify all completed
        from vibe.research_journal.query import ExperimentQuery
        query = ExperimentQuery(registry)
        completed = query.by_status(ExperimentStatus.COMPLETED).execute()
        assert len(completed) >= 9
    
    def test_query_sweep_results_by_sharpe(self, tmp_path):
        """Should be able to query best results by Sharpe."""
        registry = ResearchRegistry(tmp_path)
        tracker = ParameterSweepExperimentTracker(registry=registry)
        
        # Create parent
        parent_id = tracker.create_parent_experiment(
            strategy_name="TestStrategy",
            strategy_version="1.0",
            base_parameters={},
            dataset_config={}
        )
        
        # Create 3 variations with different Sharpe ratios
        for i in range(3):
            var_id = tracker.create_variation_experiment(
                variation_number=i + 1,
                strategy_name="TestStrategy",
                strategy_version="1.0",
                variation_parameters={"param": i},
                dataset_config={}
            )
            
            tracker.complete_variation_experiment(
                experiment_id=var_id,
                trades=[],
                metrics={"sharpe": 1.5 - i * 0.3},
                rank=i + 1,
                conclusion=f"Variation {i+1}"
            )
        
        # Query by Sharpe range
        from vibe.research_journal.query import ExperimentQuery
        query = ExperimentQuery(registry)
        high_sharpe = (query
                       .by_result_quality("sharpe", 1.0, 1.6)
                       .execute())
        
        assert len(high_sharpe) >= 2  # First two should be > 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
