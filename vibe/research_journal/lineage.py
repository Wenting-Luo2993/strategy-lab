"""Lineage graph for tracking experiment relationships and dependencies.

Maintains parent/child relationships between experiments with cycle detection
and ancestor/descendant traversal capabilities.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from vibe.research_journal.models import Experiment
from vibe.research_journal.persistence import ensure_research_directories, load_experiment

logger = logging.getLogger(__name__)


class CycleDetectedError(Exception):
    """Raised when a cycle is detected in experiment lineage."""

    pass


class LineageGraph:
    """Directed graph of experiment relationships.

    Maintains parent/child relationships and provides traversal methods.
    Detects cycles on construction.
    """

    def __init__(self, experiments: List[Experiment]):
        """Initialize lineage graph from list of experiments.

        Args:
            experiments: List of Experiment instances

        Raises:
            CycleDetectedError: If cycle exists in lineage
        """
        self.experiments: Dict[str, Experiment] = {e.id: e for e in experiments}
        self.children: Dict[str, List[str]] = {}  # parent_id -> [child_ids]
        self.parents: Dict[str, Optional[str]] = {}  # exp_id -> parent_id

        # Build graph
        for exp in experiments:
            self.parents[exp.id] = exp.parent_experiment_id
            if exp.parent_experiment_id:
                if exp.parent_experiment_id not in self.children:
                    self.children[exp.parent_experiment_id] = []
                self.children[exp.parent_experiment_id].append(exp.id)

        # Validate no cycles
        self.validate_no_cycles()

    def validate_no_cycles(self) -> None:
        """Detect cycles using DFS.

        Raises:
            CycleDetectedError: If cycle detected
        """
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)

            # Check children (descendants)
            for child_id in self.children.get(node, []):
                if child_id not in visited:
                    if has_cycle(child_id):
                        return True
                elif child_id in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for exp_id in self.experiments:
            if exp_id not in visited:
                if has_cycle(exp_id):
                    raise CycleDetectedError(
                        f"Cycle detected in experiment lineage starting from {exp_id}"
                    )

    def get_children(self, experiment_id: str) -> List[str]:
        """Get direct children of experiment.

        Args:
            experiment_id: ID of parent experiment

        Returns:
            List of child experiment IDs
        """
        return self.children.get(experiment_id, [])

    def get_descendants(self, experiment_id: str) -> List[str]:
        """Get all descendants recursively.

        Args:
            experiment_id: ID of root experiment

        Returns:
            List of all descendant IDs (not including root)
        """
        descendants = []
        visited = set()

        def traverse(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)

            for child_id in self.children.get(node_id, []):
                descendants.append(child_id)
                traverse(child_id)

        traverse(experiment_id)
        return descendants

    def get_parent(self, experiment_id: str) -> Optional[str]:
        """Get direct parent of experiment.

        Args:
            experiment_id: ID of experiment

        Returns:
            Parent experiment ID or None if root
        """
        return self.parents.get(experiment_id)

    def get_ancestors(self, experiment_id: str) -> List[str]:
        """Get all ancestors up to root.

        Args:
            experiment_id: ID of experiment

        Returns:
            List of ancestor IDs from immediate parent to root
        """
        ancestors = []
        current = self.parents.get(experiment_id)

        while current is not None:
            ancestors.append(current)
            current = self.parents.get(current)

        return ancestors

    def find_root(self, experiment_id: str) -> str:
        """Find root experiment (no parent) for given experiment.

        Args:
            experiment_id: ID of experiment

        Returns:
            Root experiment ID
        """
        current = experiment_id
        while self.parents.get(current) is not None:
            current = self.parents[current]
        return current

    def get_depth(self, experiment_id: str) -> int:
        """Get depth from root (root = 0).

        Args:
            experiment_id: ID of experiment

        Returns:
            Distance from root experiment
        """
        return len(self.get_ancestors(experiment_id))

    def to_dict(self) -> Dict[str, any]:
        """Serialize lineage structure to dictionary.

        Returns:
            Dict with experiment relationships
        """
        return {
            "experiments": {exp_id: exp.id for exp_id, exp in self.experiments.items()},
            "children": self.children,
            "parents": self.parents,
        }


def build_lineage_graph(research_root: Optional[Path] = None) -> LineageGraph:
    """Build lineage graph from all experiments in research directory.

    Args:
        research_root: Optional override for research directory root

    Returns:
        LineageGraph instance with all experiments

    Raises:
        CycleDetectedError: If cycles detected in lineage
    """
    research_root = ensure_research_directories(research_root)
    experiments_dir = research_root / "experiments"

    experiments = []
    if experiments_dir.exists():
        for yaml_file in experiments_dir.glob("*.yaml"):
            exp_id = yaml_file.stem
            exp = load_experiment(exp_id, research_root)
            experiments.append(exp)

    graph = LineageGraph(experiments)

    # Warn if deep nesting detected
    for exp in experiments:
        depth = graph.get_depth(exp.id)
        if depth > 5:
            logger.warning(
                f"Deep experiment nesting detected: {exp.id} at depth {depth}. "
                f"May indicate design issue (consider flattening lineage)."
            )

    return graph
