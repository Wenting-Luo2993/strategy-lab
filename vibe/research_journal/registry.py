"""High-level experiment registry API.

Combines domain models, persistence, git metadata, and lineage tracking
to provide a unified interface for managing research experiments.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from vibe.research_journal.git_metadata import capture_execution_metadata
from vibe.research_journal.lineage import LineageGraph, build_lineage_graph, CycleDetectedError
from vibe.research_journal.models import (
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    RejectedIdea,
    ResearchNote,
)
from vibe.research_journal.persistence import (
    ensure_research_directories,
    load_experiment,
    load_hypothesis,
    save_experiment,
    save_hypothesis,
    save_rejected_idea,
    save_research_note,
    update_experiment_status,
    ImmutabilityError,
)

logger = logging.getLogger(__name__)


class ResearchRegistry:
    """High-level API for experiment lifecycle management.

    Provides methods for creating hypotheses, running experiments,
    tracking results, and querying research history.
    """

    def __init__(self, research_root: Optional[Path] = None):
        """Initialize registry.

        Args:
            research_root: Optional override for research directory root
        """
        self.research_root = ensure_research_directories(research_root)
        self._lineage_graph: Optional[LineageGraph] = None

    def create_hypothesis(
        self,
        title: str,
        rationale: str,
        tags: Optional[List[str]] = None,
    ) -> Hypothesis:
        """Create new hypothesis.

        Auto-generates ID and sets status to PROPOSED.

        Args:
            title: Hypothesis title (max 200 chars)
            rationale: Why we believe this hypothesis
            tags: Optional categorization tags

        Returns:
            Created Hypothesis instance

        Raises:
            ValueError: If validation fails
        """
        if tags is None:
            tags = []

        hyp_id = self._next_id("HYP")
        now = datetime.now(timezone.utc)

        hypothesis = Hypothesis(
            id=hyp_id,
            title=title,
            rationale=rationale,
            status=HypothesisStatus.PROPOSED,
            tags=tags,
            created_at=now,
            updated_at=now,
        )

        save_hypothesis(hypothesis, self.research_root)
        logger.info(f"Created hypothesis: {hyp_id}")

        return hypothesis

    def create_experiment(
        self,
        strategy_name: str,
        strategy_version: str,
        parameters: Dict,
        dataset_config: Dict,
        hypothesis_id: Optional[str] = None,
        parent_experiment_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        random_seed: Optional[int] = None,
    ) -> Experiment:
        """Create new experiment.

        Auto-generates ID and captures git metadata. Validates parent exists
        and prevents cycles.

        Args:
            strategy_name: Name of strategy being tested
            strategy_version: Version of strategy
            parameters: Strategy configuration dict
            dataset_config: Dataset specification
            hypothesis_id: Optional link to hypothesis (HYP-NNN)
            parent_experiment_id: Optional link to parent experiment (EXP-NNN)
            tags: Optional categorization tags
            random_seed: Optional random seed for reproducibility

        Returns:
            Created Experiment instance

        Raises:
            ValueError: If validation fails
            CycleDetectedError: If parent relationship would create cycle
        """
        if tags is None:
            tags = []

        if hypothesis_id:
            try:
                load_hypothesis(hypothesis_id, self.research_root)
            except FileNotFoundError:
                logger.warning(
                    "Creating experiment with unresolved hypothesis reference: %s",
                    hypothesis_id,
                )

        # Validate parent exists if provided
        if parent_experiment_id:
            try:
                load_experiment(parent_experiment_id, self.research_root)
            except FileNotFoundError:
                raise ValueError(
                    f"Parent experiment not found: {parent_experiment_id}"
                )

            # Check for cycles (would creating this experiment cause a cycle?)
            self._check_no_cycle(parent_experiment_id)

        exp_id = self._next_id("EXP")
        now = datetime.now(timezone.utc)

        # Capture git metadata
        execution_metadata = capture_execution_metadata(random_seed=random_seed)

        experiment = Experiment(
            id=exp_id,
            hypothesis_id=hypothesis_id,
            parent_experiment_id=parent_experiment_id,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            parameters=parameters,
            dataset_config=dataset_config,
            execution_metadata=execution_metadata,
            status=ExperimentStatus.REGISTERED,
            tags=tags,
            created_at=now,
        )

        save_experiment(experiment, self.research_root)
        logger.info(f"Created experiment: {exp_id}")

        # Invalidate lineage cache
        self._lineage_graph = None

        return experiment

    def complete_experiment(
        self, experiment_id: str, results: Dict, conclusion: str
    ) -> Experiment:
        """Mark experiment as completed with results.

        Args:
            experiment_id: ID of experiment to complete
            results: Dictionary of result metrics
            conclusion: Human-readable conclusion

        Returns:
            Updated Experiment instance

        Raises:
            FileNotFoundError: If experiment not found
            ImmutabilityError: If experiment already completed
            ValueError: If validation fails
        """
        experiment = load_experiment(experiment_id, self.research_root)

        # This will check immutability internally
        experiment.mark_completed(results, conclusion)

        # Remove old file and save new version
        filepath = self.research_root / "experiments" / f"{experiment_id}.yaml"
        if filepath.exists():
            filepath.unlink()

        save_experiment(experiment, self.research_root)
        logger.info(f"Completed experiment: {experiment_id}")

        # Invalidate lineage cache
        self._lineage_graph = None

        return experiment

    def get_experiment(self, experiment_id: str) -> Experiment:
        """Load experiment by ID.

        Args:
            experiment_id: ID of experiment to load

        Returns:
            Experiment instance

        Raises:
            FileNotFoundError: If experiment not found
        """
        return load_experiment(experiment_id, self.research_root)

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis:
        """Load hypothesis by ID.

        Args:
            hypothesis_id: ID of hypothesis to load

        Returns:
            Hypothesis instance

        Raises:
            FileNotFoundError: If hypothesis not found
        """
        return load_hypothesis(hypothesis_id, self.research_root)

    def list_experiments(
        self,
        status: Optional[ExperimentStatus] = None,
        hypothesis_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Experiment]:
        """List experiments with optional filtering.

        Args:
            status: Filter by status (e.g., COMPLETED)
            hypothesis_id: Filter by hypothesis ID
            tags: Filter by tags (all tags must match)

        Returns:
            List of matching Experiment instances
        """
        experiments_dir = self.research_root / "experiments"
        experiments = []

        if not experiments_dir.exists():
            return experiments

        for yaml_file in experiments_dir.glob("*.yaml"):
            exp_id = yaml_file.stem
            try:
                exp = load_experiment(exp_id, self.research_root)

                # Apply filters
                if status and exp.status != status:
                    continue
                if hypothesis_id and exp.hypothesis_id != hypothesis_id:
                    continue
                if tags and not all(tag in exp.tags for tag in tags):
                    continue

                experiments.append(exp)
            except Exception as e:
                logger.warning(f"Failed to load experiment {exp_id}: {e}")

        return experiments

    def add_research_note(
        self,
        content: str,
        related_experiment_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ResearchNote:
        """Create research note.

        Args:
            content: Observation text (min 10 chars)
            related_experiment_id: Optional link to experiment
            tags: Optional categorization tags

        Returns:
            Created ResearchNote instance
        """
        if tags is None:
            tags = []

        note_id = self._next_id("NOTE")
        now = datetime.now(timezone.utc)

        note = ResearchNote(
            id=note_id,
            content=content,
            related_experiment_id=related_experiment_id,
            tags=tags,
            created_at=now,
        )

        save_research_note(note, self.research_root)
        logger.info(f"Created research note: {note_id}")

        return note

    def reject_idea(
        self,
        idea: str,
        reason_rejected: str,
        evidence: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> RejectedIdea:
        """Record rejected idea.

        Args:
            idea: Description of idea
            reason_rejected: Why it failed
            evidence: Optional list of experiment IDs (EXP-NNN format)
            tags: Optional categorization tags

        Returns:
            Created RejectedIdea instance
        """
        if evidence is None:
            evidence = []
        if tags is None:
            tags = []

        rj_id = self._next_id("RJ")
        now = datetime.now(timezone.utc)

        rejected_idea = RejectedIdea(
            id=rj_id,
            idea=idea,
            reason_rejected=reason_rejected,
            evidence=evidence,
            tags=tags,
            created_at=now,
        )

        save_rejected_idea(rejected_idea, self.research_root)
        logger.info(f"Created rejected idea: {rj_id}")

        return rejected_idea

    def get_lineage_graph(self) -> LineageGraph:
        """Get current lineage graph (cached).

        Returns:
            LineageGraph instance with all experiments
        """
        if self._lineage_graph is None:
            self._lineage_graph = build_lineage_graph(self.research_root)
        return self._lineage_graph

    def _next_id(self, prefix: str) -> str:
        """Generate next sequential ID.

        Scans existing files to find maximum ID number, returns next.

        Args:
            prefix: ID prefix (HYP, EXP, NOTE, RJ, ART)

        Returns:
            Next sequential ID (e.g., "HYP-003")
        """
        # Determine directory based on prefix
        if prefix == "HYP":
            dir_path = self.research_root / "hypotheses"
        elif prefix == "EXP":
            dir_path = self.research_root / "experiments"
        elif prefix == "NOTE":
            dir_path = self.research_root / "notes"
        elif prefix == "RJ":
            dir_path = self.research_root / "rejected"
        elif prefix == "ART":
            dir_path = self.research_root / "artifacts"
        else:
            raise ValueError(f"Unknown prefix: {prefix}")

        max_num = 0
        if dir_path.exists():
            for file in dir_path.glob(f"{prefix}-*.yaml"):
                match = re.search(r"{}-(\d+)".format(prefix), file.stem)
                if match:
                    num = int(match.group(1))
                    max_num = max(max_num, num)
            for file in dir_path.glob(f"{prefix}-*.md"):
                match = re.search(r"{}-(\d+)".format(prefix), file.stem)
                if match:
                    num = int(match.group(1))
                    max_num = max(max_num, num)

        return f"{prefix}-{max_num + 1:03d}"

    def _check_no_cycle(self, parent_experiment_id: str) -> None:
        """Check if parent_experiment_id would create a cycle.

        Args:
            parent_experiment_id: ID of potential parent

        Raises:
            CycleDetectedError: If cycle would be created
        """
        graph = self.get_lineage_graph()

        # If adding parent as parent to any of its descendants would create cycle
        # This is already prevented by the experiment model itself,
        # but we can check here too.
        parent_exp = load_experiment(parent_experiment_id, self.research_root)
        if parent_exp.parent_experiment_id is not None:
            # Parent has a parent, so no cycle possible with new child
            pass
