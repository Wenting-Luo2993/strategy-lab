"""
Tests for Query API (Stage 6)

P0 Tests (Critical):
  - Basic tag filtering
  - Parameter filtering with nested paths
  - Result quality range filtering
  - Date range filtering
  - Query combination (intersection)

P1 Tests (Edge Cases):
  - Case-insensitive tag matching
  - Empty result sets
  - Multiple criteria combinations
  - Chaining multiple queries
"""

import pytest
from datetime import datetime, timezone, timedelta
from vibe.research_journal.models import (
    Experiment,
    ExperimentStatus,
    ExecutionMetadata,
)
from vibe.research_journal.query import ExperimentQuery, HypothesisQuery
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.persistence import ensure_research_directories
from pathlib import Path
import tempfile
import shutil
import stat


@pytest.fixture
def temp_research_dir():
    """Create temporary research directory for tests."""
    temp_dir = tempfile.mkdtemp()
    research_root = Path(temp_dir) / "research"
    ensure_research_directories(research_root)
    yield research_root
    
    # Make files writable before deleting (completed experiments are read-only)
    def handle_remove_readonly(func, path, exc):
        import os
        if not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
            func(path)
        else:
            raise
    
    shutil.rmtree(temp_dir, onerror=handle_remove_readonly)


@pytest.fixture
def registry_with_experiments(temp_research_dir):
    """Create registry with sample experiments."""
    registry = ResearchRegistry(temp_research_dir)
    
    # Create hypothesis
    hyp = registry.create_hypothesis(
        title="Test ORB strategy",
        rationale="ORB shows promise in trending markets",
        tags=["orb", "volume-based"]
    )
    
    # Create parent experiment
    parent = registry.create_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.0.0",
        parameters={"orb_minutes": 5, "take_profit": 2},
        dataset_config={"symbols": ["QQQ"], "period": "2024"},
        hypothesis_id=hyp.id,
        tags=["parent", "baseline"]
    )
    
    # Create child experiment
    child = registry.create_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.1.0",
        parameters={"orb_minutes": 10, "take_profit": 3, "atr_filter": True},
        dataset_config={"symbols": ["QQQ"], "period": "2024"},
        hypothesis_id=hyp.id,
        parent_experiment_id=parent.id,
        tags=["child", "optimization"]
    )
    
    # Complete some experiments with results
    registry.complete_experiment(
        parent.id,
        results={
            "sharpe_ratio": 1.2,
            "expectancy_r": 0.05,
            "max_drawdown": 0.15,
            "total_trades": 127,
            "win_rate": 0.52
        },
        conclusion="Baseline established."
    )
    
    registry.complete_experiment(
        child.id,
        results={
            "sharpe_ratio": 1.8,
            "expectancy_r": 0.08,
            "max_drawdown": 0.10,
            "total_trades": 125,
            "win_rate": 0.55
        },
        conclusion="Improvement achieved."
    )
    
    # Create another hypothesis
    hyp2 = registry.create_hypothesis(
        title="Test ATR-based strategy",
        rationale="ATR captures volatility",
        tags=["atr", "volatility"]
    )
    
    # Create experiment for second hypothesis
    exp2 = registry.create_experiment(
        strategy_name="ATRStrategy",
        strategy_version="1.0.0",
        parameters={"atr_period": 14, "atr_multiple": 2.0},
        dataset_config={"symbols": ["SPY"], "period": "2024"},
        hypothesis_id=hyp2.id,
        tags=["exploration"]
    )
    
    return registry, {
        "parent": parent,
        "child": child,
        "exp2": exp2,
        "hyp1": hyp,
        "hyp2": hyp2
    }


class TestExperimentQueryBasic:
    """P0: Basic filtering tests."""
    
    def test_query_by_tag_single(self, registry_with_experiments):
        """Test filtering by single tag."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_tag("baseline").execute()
        
        assert len(results) == 1
        assert results[0].id == exps["parent"].id
    
    def test_query_by_tag_case_insensitive(self, registry_with_experiments):
        """Test that tag matching is case-insensitive."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_tag("BASELINE").execute()  # uppercase
        
        assert len(results) == 1
        assert results[0].id == exps["parent"].id
    
    def test_query_by_status(self, registry_with_experiments):
        """Test filtering by status."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_status(ExperimentStatus.COMPLETED).execute()
        
        assert len(results) == 2
        ids = {r.id for r in results}
        assert exps["parent"].id in ids
        assert exps["child"].id in ids
    
    def test_query_by_hypothesis(self, registry_with_experiments):
        """Test filtering by hypothesis."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_hypothesis(exps["hyp1"].id).execute()
        
        assert len(results) == 2  # parent and child
        ids = {r.id for r in results}
        assert exps["parent"].id in ids
        assert exps["child"].id in ids


class TestExperimentQueryParameter:
    """P0: Parameter filtering tests."""
    
    def test_query_by_parameter_simple(self, registry_with_experiments):
        """Test filtering by simple (non-nested) parameter."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_parameter("orb_minutes", 5).execute()
        
        assert len(results) == 1
        assert results[0].id == exps["parent"].id
    
    def test_query_by_parameter_nested(self, registry_with_experiments):
        """Test filtering by nested parameter path (e.g., 'strategy.atr_filter')."""
        registry, exps = registry_with_experiments
        
        # Query for nested parameter that exists only in child
        query = ExperimentQuery(registry)
        results = query.by_parameter("atr_filter", True).execute()
        
        assert len(results) == 1
        assert results[0].id == exps["child"].id
    
    def test_query_by_parameter_no_match(self, registry_with_experiments):
        """Test parameter filtering with no matches."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_parameter("nonexistent", 999).execute()
        
        assert len(results) == 0


class TestExperimentQueryResults:
    """P0: Result quality filtering tests."""
    
    def test_query_by_result_quality_range(self, registry_with_experiments):
        """Test filtering by result metric range."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        # Find experiments with Sharpe ratio between 1.5 and 2.0
        results = query.by_result_quality("sharpe_ratio", 1.5, 2.0).execute()
        
        assert len(results) == 1
        assert results[0].id == exps["child"].id
    
    def test_query_by_result_quality_single_value(self, registry_with_experiments):
        """Test filtering by exact result value."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        # Find experiments with exact Sharpe ratio
        results = query.by_result_quality("sharpe_ratio", 1.2, 1.2).execute()
        
        assert len(results) == 1
        assert results[0].id == exps["parent"].id
    
    def test_query_by_result_quality_uncompleted(self, registry_with_experiments):
        """Test that uncompleted experiments are excluded."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        # Experiment 2 is not completed, so shouldn't appear
        results = query.by_result_quality("sharpe_ratio", 0, 3.0).execute()
        
        assert len(results) == 2
        ids = {r.id for r in results}
        assert exps["parent"].id in ids
        assert exps["child"].id in ids
        assert exps["exp2"].id not in ids


class TestExperimentQueryDate:
    """P0: Date range filtering tests."""
    
    def test_query_by_date_range(self, registry_with_experiments):
        """Test filtering by date range."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc) + timedelta(hours=1)
        results = query.by_date_range(start, end).execute()
        
        # All experiments created within this range
        assert len(results) >= 3
    
    def test_query_by_date_range_empty(self, registry_with_experiments):
        """Test date range that excludes all experiments."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        start = datetime.now(timezone.utc) + timedelta(days=1)
        end = datetime.now(timezone.utc) + timedelta(days=2)
        results = query.by_date_range(start, end).execute()
        
        assert len(results) == 0


class TestExperimentQueryCombination:
    """P0: Query combination (intersection) tests."""
    
    def test_query_combine_tag_and_status(self, registry_with_experiments):
        """Test combining tag and status filters."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_tag("baseline").by_status(ExperimentStatus.COMPLETED).execute()
        
        assert len(results) == 1
        assert results[0].id == exps["parent"].id
    
    def test_query_combine_multiple(self, registry_with_experiments):
        """Test chaining multiple filter criteria."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = (query
                   .by_hypothesis(exps["hyp1"].id)
                   .by_status(ExperimentStatus.COMPLETED)
                   .by_tag("optimization")
                   .execute())
        
        assert len(results) == 1
        assert results[0].id == exps["child"].id
    
    def test_query_intersection_empty(self, registry_with_experiments):
        """Test combination that yields no results."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_tag("baseline").by_tag("optimization").execute()
        
        # No experiment has both tags (intersection of incompatible criteria)
        assert len(results) == 0
    
    def test_static_combine_method(self, registry_with_experiments):
        """Test static combine() method for query intersection."""
        registry, exps = registry_with_experiments
        
        q1 = ExperimentQuery(registry).by_status(ExperimentStatus.COMPLETED)
        q2 = ExperimentQuery(registry).by_tag("optimization")
        
        results = ExperimentQuery.combine(registry, q1, q2)
        
        assert len(results) == 1
        assert results[0].id == exps["child"].id


class TestExperimentQueryChaining:
    """P1: Query chaining and reset tests."""
    
    def test_query_reset_between_executions(self, registry_with_experiments):
        """Test that same query object can be executed multiple times."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry).by_tag("baseline")
        
        results1 = query.execute()
        results2 = query.execute()
        
        assert len(results1) == 1
        assert len(results2) == 1
        assert results1[0].id == results2[0].id
    
    def test_query_new_instance_independent(self, registry_with_experiments):
        """Test that query instances are independent."""
        registry, exps = registry_with_experiments
        
        q1 = ExperimentQuery(registry).by_tag("baseline")
        q2 = ExperimentQuery(registry).by_tag("optimization")
        
        r1 = q1.execute()
        r2 = q2.execute()
        
        assert r1[0].id != r2[0].id


class TestHypothesisQuery:
    """P1: Hypothesis query tests."""
    
    def test_hypothesis_query_by_tag(self, registry_with_experiments):
        """Test filtering hypotheses by tag."""
        registry, exps = registry_with_experiments
        
        query = HypothesisQuery(registry)
        results = query.by_tag("orb").execute()
        
        assert len(results) >= 1
        ids = {h.id for h in results}
        assert exps["hyp1"].id in ids
    
    def test_hypothesis_query_by_status(self, registry_with_experiments):
        """Test filtering hypotheses by status."""
        from vibe.research_journal.models import HypothesisStatus
        
        registry, exps = registry_with_experiments
        
        query = HypothesisQuery(registry)
        results = query.by_status(HypothesisStatus.PROPOSED).execute()
        
        assert len(results) >= 2
        ids = {h.id for h in results}
        assert exps["hyp1"].id in ids
        assert exps["hyp2"].id in ids


class TestEdgeCases:
    """P1: Edge case tests."""
    
    def test_query_empty_registry(self, temp_research_dir):
        """Test querying empty registry."""
        registry = ResearchRegistry(temp_research_dir)
        
        query = ExperimentQuery(registry)
        results = query.by_tag("anything").execute()
        
        assert len(results) == 0
    
    def test_query_by_tag_multiple_matches(self, registry_with_experiments):
        """Test tag filtering when multiple experiments match."""
        registry, exps = registry_with_experiments
        
        query = ExperimentQuery(registry)
        results = query.by_tag("parent").execute()  # Parent has 'parent' tag
        
        # Should match at least parent
        assert len(results) >= 1
        assert exps["parent"].id in {r.id for r in results}
    
    def test_query_special_characters_in_tag(self, temp_research_dir):
        """Test querying with special characters in tag."""
        registry = ResearchRegistry(temp_research_dir)
        
        hyp = registry.create_hypothesis(
            title="Test special chars",
            rationale="Testing special characters in tags",
            tags=["test-v1.0", "test_var", "test.prod"]
        )
        
        exp = registry.create_experiment(
            strategy_name="Test",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
            hypothesis_id=hyp.id,
            tags=["test-v1.0", "test_var"]
        )
        
        query = ExperimentQuery(registry)
        results = query.by_tag("test-v1.0").execute()
        
        assert len(results) == 1
        assert results[0].id == exp.id
