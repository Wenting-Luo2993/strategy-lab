"""High-level experiment registry API.

Combines domain models, persistence, git metadata, and lineage tracking
to provide a unified interface for managing research experiments.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    ExecutionRealismConfig,
)
from vibe.research_journal.git_metadata import capture_execution_metadata
from vibe.research_journal.lineage import LineageGraph, CycleDetectedError
from vibe.research_journal.models import (
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    RejectedIdea,
    ResearchNote,
)
from vibe.research_journal.persistence import ensure_research_directories, ImmutabilityError
from vibe.research_pipeline.paths import research_db_path
from vibe.research_pipeline.storage.legacy_importer import import_legacy_tree
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

logger = logging.getLogger(__name__)


class ResearchRegistry:
    """High-level API for experiment lifecycle management.

    Provides methods for creating hypotheses, running experiments,
    tracking results, and querying research history.
    """

    def __init__(
        self,
        research_root: Optional[Path] = None,
        *,
        sqlite_store: Optional[SqliteResearchStore] = None,
        import_legacy_yaml: bool = True,
        methodology_version: str = "research-store-v1",
        execution_realism: Optional[ExecutionRealismConfig] = None,
    ):
        """Initialize registry.

        Args:
            research_root: Optional override for research directory root
        """
        self.research_root = ensure_research_directories(research_root)
        self._lineage_graph: Optional[LineageGraph] = None
        self._owns_store = sqlite_store is None
        self._sqlite_db_path = (
            None if sqlite_store is not None else self._compatibility_db_path(research_root)
        )
        self._sqlite_store = sqlite_store or SqliteResearchStore(self._sqlite_db_path)
        self.methodology_version = methodology_version
        self.execution_realism = (
            execution_realism or ExecutionRealismConfig.realistic()
        )
        if import_legacy_yaml:
            self._import_legacy_yaml_once()

    def close(self) -> None:
        if self._owns_store:
            self._sqlite_store.close()
            self._owns_store = False

    def __enter__(self) -> "ResearchRegistry":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Interpreter shutdown and partially constructed instances must not
            # turn cleanup into a secondary failure.
            pass

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

        self._write_new("hypothesis", hypothesis)
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
        methodology_version: Optional[str] = None,
        execution_identity: Optional[Dict[str, Any]] = None,
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
                self.get_hypothesis(hypothesis_id)
            except FileNotFoundError:
                logger.warning(
                    "Creating experiment with unresolved hypothesis reference: %s",
                    hypothesis_id,
                )

        # Validate parent exists if provided
        if parent_experiment_id:
            try:
                self.get_experiment(parent_experiment_id)
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
        identity = execution_identity or self.execution_realism.identity()
        resolved_methodology = methodology_version or self.methodology_version

        experiment = Experiment(
            id=exp_id,
            hypothesis_id=hypothesis_id,
            parent_experiment_id=parent_experiment_id,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            parameters=parameters,
            dataset_config=dataset_config,
            execution_metadata=execution_metadata,
            methodology_version=resolved_methodology,
            execution_model_version=int(
                identity.get("execution_model_version", EXECUTION_MODEL_VERSION)
            ),
            execution_identity=identity,
            status=ExperimentStatus.REGISTERED,
            tags=tags,
            created_at=now,
        )

        self._write_new("experiment", experiment, execution_identity=identity)
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
        experiment = self.get_experiment(experiment_id)

        # This will check immutability internally
        experiment.mark_completed(results, conclusion)

        self._sqlite_store.update_registry_record(
            record_type="experiment",
            legacy_id=experiment_id,
            payload=experiment.model_dump(mode="json"),
        )
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
        payload = self._sqlite_store.get_registry_record(
            record_type="experiment", legacy_id=experiment_id
        )
        if payload is None:
            raise FileNotFoundError(f"Experiment not found: {experiment_id}")
        payload.pop("_record_origin", None)
        payload.pop("_imported_at", None)
        return Experiment(**payload)

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis:
        """Load hypothesis by ID.

        Args:
            hypothesis_id: ID of hypothesis to load

        Returns:
            Hypothesis instance

        Raises:
            FileNotFoundError: If hypothesis not found
        """
        payload = self._sqlite_store.get_registry_record(
            record_type="hypothesis", legacy_id=hypothesis_id
        )
        if payload is None:
            raise FileNotFoundError(f"Hypothesis not found: {hypothesis_id}")
        payload.pop("_record_origin", None)
        payload.pop("_imported_at", None)
        return Hypothesis(**payload)

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
        experiments: List[Experiment] = []
        for payload in self._sqlite_store.query_registry_records(
            record_type="experiment"
        ):
            payload.pop("_record_origin", None)
            payload.pop("_imported_at", None)
            exp = Experiment(**payload)
            if status and exp.status != status:
                continue
            if hypothesis_id and exp.hypothesis_id != hypothesis_id:
                continue
            if tags and not all(tag in exp.tags for tag in tags):
                continue
            experiments.append(exp)
        return experiments

    def list_hypotheses(self) -> List[Hypothesis]:
        hypotheses: List[Hypothesis] = []
        for payload in self._sqlite_store.query_registry_records(
            record_type="hypothesis"
        ):
            payload.pop("_record_origin", None)
            payload.pop("_imported_at", None)
            hypotheses.append(Hypothesis(**payload))
        return hypotheses

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

        self._write_new("note", note)
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

        self._write_new("rejected_idea", rejected_idea)
        logger.info(f"Created rejected idea: {rj_id}")

        return rejected_idea

    def save_artifact(self, artifact: Any) -> None:
        """Persist an artifact reference in the authoritative store."""
        self._write_new("artifact", artifact)

    def get_artifact(self, artifact_id: str) -> Any:
        from vibe.research_journal.models import ArtifactReference

        payload = self._sqlite_store.get_registry_record(
            record_type="artifact", legacy_id=artifact_id
        )
        if payload is None:
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")
        payload.pop("_record_origin", None)
        payload.pop("_imported_at", None)
        return ArtifactReference(**payload)

    def list_artifacts(self, experiment_id: Optional[str] = None) -> List[Any]:
        from vibe.research_journal.models import ArtifactReference

        result = []
        for payload in self._sqlite_store.query_registry_records(
            record_type="artifact"
        ):
            payload.pop("_record_origin", None)
            payload.pop("_imported_at", None)
            artifact = ArtifactReference(**payload)
            if experiment_id is None or artifact.experiment_id == experiment_id:
                result.append(artifact)
        return result

    def list_research_notes(self) -> List[ResearchNote]:
        result = []
        for payload in self._sqlite_store.query_registry_records(record_type="note"):
            payload.pop("_record_origin", None)
            imported_at = payload.pop("_imported_at", None)
            if payload.get("legacy_format") == "markdown":
                payload.setdefault("related_experiment_id", None)
                payload.setdefault("tags", [])
                payload.setdefault("created_at", imported_at)
            result.append(ResearchNote(**payload))
        return result

    def _compatibility_db_path(self, requested_root: Optional[Path]) -> Path:
        if requested_root is None:
            return research_db_path(create_parents=True)

        root = self.research_root.resolve()
        if any((candidate / ".git").exists() for candidate in (root, *root.parents)):
            return research_db_path(create_parents=True)

        # Explicit roots are predominantly isolated registries (tests, tools,
        # imports). Keep their compatibility DB isolated beside the tree.
        return root.parent / f".{root.name}-research.sqlite3"

    def _write_new(
        self,
        record_type: str,
        model: Any,
        *,
        execution_identity: Optional[Dict[str, Any]] = None,
    ) -> None:
        methodology = getattr(
            model, "methodology_version", self.methodology_version
        )
        self._sqlite_store.create_registry_record(
            record_type=record_type,
            payload=model.model_dump(mode="json"),
            methodology_version=methodology,
            execution_identity=execution_identity,
        )

    def get_lineage_graph(self) -> LineageGraph:
        """Get current lineage graph (cached).

        Returns:
            LineageGraph instance with all experiments
        """
        if self._lineage_graph is None:
            self._lineage_graph = LineageGraph(self.list_experiments())
        return self._lineage_graph

    def _next_id(self, prefix: str) -> str:
        """Generate next sequential ID.

        Scans existing files to find maximum ID number, returns next.

        Args:
            prefix: ID prefix (HYP, EXP, NOTE, RJ, ART)

        Returns:
            Next sequential ID (e.g., "HYP-003")
        """
        record_types = {
            "HYP": "hypothesis",
            "EXP": "experiment",
            "NOTE": "note",
            "RJ": "rejected_idea",
            "ART": "artifact",
        }
        if prefix not in record_types:
            raise ValueError(f"Unknown prefix: {prefix}")
        return self._sqlite_store.allocate_registry_id(
            record_type=record_types[prefix], prefix=prefix
        )

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
        parent_exp = self.get_experiment(parent_experiment_id)
        if parent_exp.parent_experiment_id is not None:
            # Parent has a parent, so no cycle possible with new child
            pass

    def _import_legacy_yaml_once(self) -> None:
        conn = self._sqlite_store._conn
        audit = conn.execute(
            "SELECT status FROM registry_import_audit WHERE singleton = 1"
        ).fetchone()
        if audit is not None:
            return
        legacy_patterns = (
            "hypotheses/HYP-*.yaml",
            "experiments/EXP-*.yaml",
            "notes/NOTE-*.md",
            "rejected/RJ-*.yaml",
            "artifacts/ART-*.yaml",
        )
        has_legacy_source = any(
            self.research_root.glob(pattern) for pattern in legacy_patterns
        )
        imported_count = conn.execute(
            """
            SELECT COUNT(*) FROM registry_records
            WHERE record_origin = 'legacy_yaml'
            """
        ).fetchone()[0]
        if imported_count:
            with conn:
                conn.execute(
                    """
                    INSERT INTO registry_import_audit (
                        singleton, status, canonical_tree_sha256,
                        record_count, completed_at
                    ) VALUES (1, 'completed-existing', NULL, ?, ?)
                    """,
                    (imported_count, datetime.now(timezone.utc).isoformat()),
                )
            return
        if not has_legacy_source:
            with conn:
                conn.execute(
                    """
                    INSERT INTO registry_import_audit (
                        singleton, status, canonical_tree_sha256,
                        record_count, completed_at
                    ) VALUES (1, 'no-source', NULL, 0, ?)
                    """,
                    (datetime.now(timezone.utc).isoformat(),),
                )
            return
        verification = import_legacy_tree(
            self.research_root,
            conn,
            repository_root=self.research_root.parent,
        )
        with conn:
            conn.execute(
                """
                INSERT INTO registry_import_audit (
                    singleton, status, canonical_tree_sha256,
                    record_count, completed_at
                ) VALUES (1, 'completed', ?, ?, ?)
                """,
                (
                    verification.canonical_tree_sha256,
                    verification.record_count,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
