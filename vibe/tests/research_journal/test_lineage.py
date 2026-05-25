"""Tests for experiment lineage graph module.

Tests focus on:
- Graph construction
- Cycle detection
- Ancestor/descendant traversal
- Depth calculation
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from vibe.research_journal.lineage import CycleDetectedError, LineageGraph
from vibe.research_journal.models import ExecutionMetadata, Experiment, ExperimentStatus


def create_experiment(
    exp_id: str, parent_id: str | None = None
) -> Experiment:
    """Helper to create experiment."""
    return Experiment(
        id=exp_id,
        parent_experiment_id=parent_id,
        strategy_name="ORBStrategy",
        strategy_version="1.0.0",
        execution_metadata=ExecutionMetadata(
            git_commit="a" * 40,
            git_branch="main",
            git_dirty=False,
            executed_at=datetime.now(timezone.utc),
            python_version="3.11.0",
        ),
        created_at=datetime.now(timezone.utc),
    )


class TestLineageGraphDetectsCycle:
    """P0: LineageGraph should detect cycles."""

    def test_lineage_graph_detects_cycle(self):
        """Creating cycle A→B→C→A should raise CycleDetectedError."""
        # Create experiments where EXP-001 → EXP-002 → EXP-003 → EXP-001
        experiments = [
            create_experiment("EXP-001", parent_id="EXP-003"),  # Cycle!
            create_experiment("EXP-002", parent_id="EXP-001"),
            create_experiment("EXP-003", parent_id="EXP-002"),
        ]

        with pytest.raises(CycleDetectedError):
            LineageGraph(experiments)

    def test_lineage_graph_no_cycle_linear_chain(self):
        """Linear chain should not raise."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002", parent_id="EXP-001"),
            create_experiment("EXP-003", parent_id="EXP-002"),
        ]

        graph = LineageGraph(experiments)
        assert graph is not None

    def test_lineage_graph_no_cycle_multi_children(self):
        """Multiple children from same parent should not raise."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002", parent_id="EXP-001"),
            create_experiment("EXP-003", parent_id="EXP-001"),
        ]

        graph = LineageGraph(experiments)
        assert graph is not None


class TestGetDescendants:
    """P0: get_descendants should return all children recursively."""

    def test_get_descendants_returns_all_children(self):
        """Should return all descendants recursively."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002", parent_id="EXP-001"),
            create_experiment("EXP-003", parent_id="EXP-002"),
            create_experiment("EXP-004", parent_id="EXP-002"),
        ]

        graph = LineageGraph(experiments)
        descendants = graph.get_descendants("EXP-001")

        assert set(descendants) == {"EXP-002", "EXP-003", "EXP-004"}

    def test_get_descendants_leaf_node_empty(self):
        """Leaf node should have no descendants."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002", parent_id="EXP-001"),
        ]

        graph = LineageGraph(experiments)
        descendants = graph.get_descendants("EXP-002")

        assert descendants == []


class TestGetAncestors:
    """P0: get_ancestors should return path to root."""

    def test_get_ancestors_returns_path_to_root(self):
        """Should return all ancestors."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002", parent_id="EXP-001"),
            create_experiment("EXP-003", parent_id="EXP-002"),
        ]

        graph = LineageGraph(experiments)
        ancestors = graph.get_ancestors("EXP-003")

        assert ancestors == ["EXP-002", "EXP-001"]

    def test_get_ancestors_root_empty(self):
        """Root experiment should have no ancestors."""
        experiments = [create_experiment("EXP-001")]

        graph = LineageGraph(experiments)
        ancestors = graph.get_ancestors("EXP-001")

        assert ancestors == []


class TestFindRoot:
    """P0: find_root should locate top-level parent."""

    def test_find_root_for_nested_experiment(self):
        """Should find root of nested hierarchy."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002", parent_id="EXP-001"),
            create_experiment("EXP-003", parent_id="EXP-002"),
        ]

        graph = LineageGraph(experiments)
        root = graph.find_root("EXP-003")

        assert root == "EXP-001"

    def test_find_root_already_root(self):
        """Root's root should be itself."""
        experiments = [create_experiment("EXP-001")]

        graph = LineageGraph(experiments)
        root = graph.find_root("EXP-001")

        assert root == "EXP-001"


class TestLineageDepthCalculation:
    """P1: Depth calculation (root=0, child=1, etc)."""

    def test_lineage_depth_calculation(self):
        """Depth should be distance from root."""
        experiments = [
            create_experiment("EXP-001"),  # depth 0
            create_experiment("EXP-002", parent_id="EXP-001"),  # depth 1
            create_experiment("EXP-003", parent_id="EXP-002"),  # depth 2
        ]

        graph = LineageGraph(experiments)

        assert graph.get_depth("EXP-001") == 0
        assert graph.get_depth("EXP-002") == 1
        assert graph.get_depth("EXP-003") == 2


class TestMultipleChildren:
    """P1: Graph should handle branching (multiple children)."""

    def test_multiple_children_from_same_parent(self):
        """Should handle branching lineage."""
        experiments = [
            create_experiment("EXP-001"),  # root
            create_experiment("EXP-002", parent_id="EXP-001"),  # child 1
            create_experiment("EXP-003", parent_id="EXP-001"),  # child 2
            create_experiment("EXP-004", parent_id="EXP-002"),  # grandchild
        ]

        graph = LineageGraph(experiments)

        children = graph.get_children("EXP-001")
        assert set(children) == {"EXP-002", "EXP-003"}

        descendants = graph.get_descendants("EXP-001")
        assert set(descendants) == {"EXP-002", "EXP-003", "EXP-004"}


class TestOrphanExperiment:
    """P1: Orphan experiment (no parent) should be root."""

    def test_orphan_experiment_has_no_parent(self):
        """Experiment with no parent_experiment_id should be root."""
        experiments = [
            create_experiment("EXP-001"),
            create_experiment("EXP-002"),  # orphan
        ]

        graph = LineageGraph(experiments)

        assert graph.get_parent("EXP-001") is None
        assert graph.get_parent("EXP-002") is None
        assert graph.get_depth("EXP-002") == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
