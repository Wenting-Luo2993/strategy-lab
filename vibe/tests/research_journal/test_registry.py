"""Tests for research registry module.

Tests focus on:
- Hypothesis and experiment creation
- Auto-ID generation
- Git metadata capture
- Immutability enforcement
- Lineage validation
- List/query operations
"""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from vibe.research_journal.lineage import CycleDetectedError
from vibe.research_journal.models import ExperimentStatus
from vibe.research_journal.persistence import ImmutabilityError
from vibe.research_journal.registry import ResearchRegistry


@pytest.fixture
def temp_registry():
    """Fixture providing temporary registry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ResearchRegistry(Path(tmpdir) / "research")


class TestCreateHypothesis:
    """P0: Create hypothesis with auto-ID generation."""

    def test_create_hypothesis_generates_unique_id(self, temp_registry):
        """Created hypotheses should have sequential IDs."""
        hyp1 = temp_registry.create_hypothesis(
            title="First hypothesis",
            rationale="First rationale that is long enough",
        )
        hyp2 = temp_registry.create_hypothesis(
            title="Second hypothesis",
            rationale="Second rationale that is long enough",
        )

        assert hyp1.id == "HYP-001"
        assert hyp2.id == "HYP-002"

    def test_create_hypothesis_sets_proposed_status(self, temp_registry):
        """New hypothesis should have PROPOSED status."""
        hyp = temp_registry.create_hypothesis(
            title="Test hypothesis",
            rationale="Test rationale that is long enough",
        )

        assert hyp.status.value == "proposed"

    def test_create_hypothesis_with_tags(self, temp_registry):
        """Hypothesis should preserve tags."""
        hyp = temp_registry.create_hypothesis(
            title="Test hypothesis",
            rationale="Test rationale that is long enough",
            tags=["orb", "volume"],
        )

        assert hyp.tags == ["orb", "volume"]


class TestCreateExperiment:
    """P0: Create experiment with auto-ID and git metadata."""

    def test_create_experiment_generates_unique_id(self, temp_registry):
        """Created experiments should have sequential IDs."""
        exp1 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={"orb_minutes": 5},
            dataset_config={"symbols": ["QQQ"]},
        )
        exp2 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={"orb_minutes": 5},
            dataset_config={"symbols": ["QQQ"]},
        )

        assert exp1.id == "EXP-001"
        assert exp2.id == "EXP-002"

    def test_create_experiment_captures_git_metadata(self, temp_registry):
        """Experiment should capture git metadata automatically."""
        exp = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        assert exp.execution_metadata.git_commit is not None
        assert len(exp.execution_metadata.git_commit) == 40
        assert exp.execution_metadata.git_branch is not None
        assert isinstance(exp.execution_metadata.git_dirty, bool)
        assert exp.execution_metadata.python_version is not None

    def test_create_experiment_registered_status(self, temp_registry):
        """New experiment should have REGISTERED status."""
        exp = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        assert exp.status == ExperimentStatus.REGISTERED

    def test_create_experiment_with_parent(self, temp_registry):
        """Experiment can link to parent experiment."""
        parent = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        child = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.1",
            parameters={"variant": "v2"},
            dataset_config={},
            parent_experiment_id=parent.id,
        )

        assert child.parent_experiment_id == parent.id

    def test_create_experiment_invalid_parent_raises(self, temp_registry):
        """Creating experiment with non-existent parent should fail."""
        with pytest.raises(ValueError) as exc_info:
            temp_registry.create_experiment(
                strategy_name="ORBStrategy",
                strategy_version="1.0.0",
                parameters={},
                dataset_config={},
                parent_experiment_id="EXP-999",
            )

        assert "not found" in str(exc_info.value).lower()


class TestCompleteExperiment:
    """P0: Complete experiment and enforce immutability."""

    def test_complete_experiment_succeeds(self, temp_registry):
        """Should be able to complete experiment."""
        exp = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        completed = temp_registry.complete_experiment(
            exp.id,
            results={"sharpe": 1.2, "expectancy_r": 0.05},
            conclusion="Edge validated",
        )

        assert completed.status == ExperimentStatus.COMPLETED
        assert completed.results_summary == {"sharpe": 1.2, "expectancy_r": 0.05}
        assert completed.conclusion == "Edge validated"

    def test_complete_experiment_enforces_immutability(self, temp_registry):
        """Cannot complete experiment twice."""
        exp = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        temp_registry.complete_experiment(
            exp.id,
            results={"sharpe": 1.2},
            conclusion="Done",
        )

        # Second complete should raise
        with pytest.raises(ValueError) as exc_info:
            temp_registry.complete_experiment(
                exp.id,
                results={"sharpe": 1.5},
                conclusion="Updated",
            )

        assert "immutable" in str(exc_info.value).lower() or "final state" in str(exc_info.value).lower()


class TestListExperiments:
    """P1: List and filter experiments."""

    def test_list_experiments_filters_by_status(self, temp_registry):
        """Should filter by status."""
        # Create experiments
        exp1 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )
        exp2 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        # Complete one
        temp_registry.complete_experiment(exp1.id, {"sharpe": 1.0}, "Done")

        # Filter by COMPLETED
        completed = temp_registry.list_experiments(status=ExperimentStatus.COMPLETED)
        assert len(completed) == 1
        assert completed[0].id == exp1.id

    def test_list_experiments_filters_by_tags(self, temp_registry):
        """Should filter by tags."""
        exp1 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
            tags=["test", "validation"],
        )
        exp2 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
            tags=["production"],
        )

        # Filter by tag
        validation = temp_registry.list_experiments(tags=["validation"])
        assert len(validation) == 1
        assert validation[0].id == exp1.id

    def test_list_experiments_empty_when_none(self, temp_registry):
        """Should return empty list when no experiments."""
        experiments = temp_registry.list_experiments()
        assert experiments == []


class TestAddResearchNote:
    """Test adding research notes."""

    def test_add_research_note_generates_id(self, temp_registry):
        """Research notes should have auto-generated IDs."""
        note1 = temp_registry.add_research_note("First observation")
        note2 = temp_registry.add_research_note("Second observation")

        assert note1.id == "NOTE-001"
        assert note2.id == "NOTE-002"

    def test_add_research_note_with_experiment_link(self, temp_registry):
        """Research note should link to experiment."""
        exp = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        note = temp_registry.add_research_note(
            "Observation about this experiment",
            related_experiment_id=exp.id,
        )

        assert note.related_experiment_id == exp.id


class TestRejectIdea:
    """Test recording rejected ideas."""

    def test_reject_idea_generates_id(self, temp_registry):
        """Rejected ideas should have auto-generated IDs."""
        rj1 = temp_registry.reject_idea("Idea 1", "Failed")
        rj2 = temp_registry.reject_idea("Idea 2", "Failed")

        assert rj1.id == "RJ-001"
        assert rj2.id == "RJ-002"

    def test_reject_idea_saves_evidence(self, temp_registry):
        """Evidence list should be preserved."""
        exp1 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )
        exp2 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        rj = temp_registry.reject_idea(
            "Add trailing stop",
            "Cut winners too early",
            evidence=[exp1.id, exp2.id],
        )

        assert rj.evidence == [exp1.id, exp2.id]


class TestLineageOperations:
    """Test lineage graph operations."""

    def test_get_lineage_graph(self, temp_registry):
        """Should build lineage graph."""
        exp1 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )
        exp2 = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.1",
            parameters={},
            dataset_config={},
            parent_experiment_id=exp1.id,
        )

        graph = temp_registry.get_lineage_graph()

        assert graph.get_parent(exp2.id) == exp1.id
        assert exp2.id in graph.get_children(exp1.id)

    def test_get_lineage_graph_caching(self, temp_registry):
        """Lineage graph should be cached."""
        graph1 = temp_registry.get_lineage_graph()
        graph2 = temp_registry.get_lineage_graph()

        assert graph1 is graph2  # Same object

    def test_lineage_graph_invalidated_on_create(self, temp_registry):
        """Lineage cache should invalidate on new experiment."""
        graph1 = temp_registry.get_lineage_graph()

        # Create new experiment
        temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={},
            dataset_config={},
        )

        graph2 = temp_registry.get_lineage_graph()

        assert graph1 is not graph2  # Different objects


class TestEndToEndWorkflow:
    """Test complete workflow from hypothesis to results."""

    def test_end_to_end_workflow(self, temp_registry):
        """Full workflow: hypothesis → experiment → complete."""
        # Create hypothesis
        hyp = temp_registry.create_hypothesis(
            title="Test ORB edge",
            rationale="Testing ORB strategy",
            tags=["orb", "validation"],
        )

        # Create experiment
        exp = temp_registry.create_experiment(
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={"orb_minutes": 5, "take_profit": 2},
            dataset_config={"symbols": ["QQQ"], "date_range": "2024"},
            hypothesis_id=hyp.id,
            tags=["test"],
        )

        assert exp.hypothesis_id == hyp.id

        # Add research note
        note = temp_registry.add_research_note(
            "Initial observation: wide OR ranges",
            related_experiment_id=exp.id,
        )

        # Complete experiment
        completed = temp_registry.complete_experiment(
            exp.id,
            results={
                "sharpe_ratio": 1.2,
                "expectancy_r": 0.05,
                "max_drawdown": 0.15,
            },
            conclusion="Edge validated, proceed to optimization",
        )

        assert completed.status == ExperimentStatus.COMPLETED

        # Verify we can load and query
        retrieved = temp_registry.get_experiment(exp.id)
        assert retrieved.status == ExperimentStatus.COMPLETED
        assert retrieved.results_summary["sharpe_ratio"] == 1.2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
