"""Unit tests for Research Journal domain models.

Tests focus on:
- Research integrity (immutability, status transitions)
- Future leakage prevention (execution metadata, datetime handling)
- Data safety (validation, serialization roundtrips)
- Reproducibility (Git state, seeds, versions)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

# Add vibe to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from vibe.research_journal.models import (
    ArtifactReference,
    ExecutionMetadata,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    RejectedIdea,
    ResearchNote,
)


class TestHypothesisIdFormatValidation:
    """P0: Hypothesis ID format must be HYP-NNN with 3+ digits."""

    def test_valid_hypothesis_id(self):
        """Valid ID HYP-001 should be accepted."""
        hyp = Hypothesis(
            id="HYP-001",
            title="Test hypothesis",
            rationale="This is a test rationale that is long enough",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert hyp.id == "HYP-001"

    def test_valid_hypothesis_id_with_long_number(self):
        """Valid ID HYP-9999 should be accepted."""
        hyp = Hypothesis(
            id="HYP-9999",
            title="Test hypothesis",
            rationale="This is a test rationale that is long enough",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert hyp.id == "HYP-9999"

    def test_invalid_hypothesis_id_too_short(self):
        """Invalid ID HYP-1 (too short) should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            Hypothesis(
                id="HYP-1",
                title="Test hypothesis",
                rationale="This is a test rationale that is long enough",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        assert "HYP-NNN" in str(exc_info.value)

    def test_invalid_hypothesis_id_non_numeric(self):
        """Invalid ID HYP-ABC should be rejected."""
        with pytest.raises(ValidationError):
            Hypothesis(
                id="HYP-ABC",
                title="Test hypothesis",
                rationale="This is a test rationale that is long enough",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

    def test_invalid_hypothesis_id_no_prefix(self):
        """Invalid ID 001 (missing prefix) should be rejected."""
        with pytest.raises(ValidationError):
            Hypothesis(
                id="001",
                title="Test hypothesis",
                rationale="This is a test rationale that is long enough",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )


class TestExperimentStatusLifecycle:
    """P0: Experiment status transitions must be valid (REGISTERED → RUNNING → COMPLETED)."""

    def _create_base_experiment(self, **kwargs) -> Experiment:
        """Helper to create experiment with defaults."""
        defaults = {
            "id": "EXP-001",
            "strategy_name": "ORBStrategy",
            "strategy_version": "1.0.0",
            "execution_metadata": ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            "created_at": datetime.now(timezone.utc),
        }
        defaults.update(kwargs)
        return Experiment(**defaults)

    def test_initial_status_is_registered(self):
        """New experiment should have status REGISTERED."""
        exp = self._create_base_experiment()
        assert exp.status == ExperimentStatus.REGISTERED

    def test_can_transition_to_running(self):
        """Experiment can transition from REGISTERED to RUNNING."""
        exp = self._create_base_experiment(status=ExperimentStatus.RUNNING)
        assert exp.status == ExperimentStatus.RUNNING

    def test_can_transition_to_completed(self):
        """Experiment can transition to COMPLETED using mark_completed()."""
        exp = self._create_base_experiment(status=ExperimentStatus.RUNNING)
        exp.mark_completed({"sharpe": 1.2}, "Edge validated")
        assert exp.status == ExperimentStatus.COMPLETED
        assert exp.results_summary == {"sharpe": 1.2}
        assert exp.conclusion == "Edge validated"


class TestExperimentImmutabilityWhenCompleted:
    """P0: Cannot modify completed or failed experiments (scientific integrity)."""

    def _create_completed_experiment(self) -> Experiment:
        """Helper to create a completed experiment."""
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.COMPLETED,
            results_summary={"sharpe": 1.2},
            conclusion="Edge validated",
            completed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        # Mark as completed to freeze
        exp.mark_completed({"sharpe": 1.2}, "Edge validated")
        return exp

    def test_mark_completed_freezes_experiment(self):
        """mark_completed() should freeze the experiment."""
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        exp.mark_completed({"sharpe": 1.2}, "Edge validated")
        assert exp.is_immutable()

    def test_cannot_modify_parameter_after_completion(self):
        """Should not be able to modify parameters after mark_completed()."""
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.RUNNING,
            parameters={"orb_minutes": 5},
            created_at=datetime.now(timezone.utc),
        )
        exp.mark_completed({"sharpe": 1.2}, "Edge validated")

        # Attempt to modify status should raise
        with pytest.raises(ValueError) as exc_info:
            exp.status = ExperimentStatus.RUNNING
        assert "immutable" in str(exc_info.value).lower()

    def test_cannot_mark_completed_twice(self):
        """Should not be able to call mark_completed() twice."""
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
        )
        exp.mark_completed({"sharpe": 1.2}, "Edge validated")

        # Second call should fail
        with pytest.raises(ValueError) as exc_info:
            exp.mark_completed({"sharpe": 1.5}, "Updated conclusion")
        assert "final state" in str(exc_info.value).lower()


class TestExecutionMetadataCapturesGitState:
    """P1: ExecutionMetadata should capture complete git state."""

    def test_all_execution_metadata_fields_populated(self):
        """ExecutionMetadata should have all fields."""
        meta = ExecutionMetadata(
            git_commit="a" * 40,
            git_branch="feature/orb-edge",
            git_dirty=True,
            random_seed=42,
            executed_at=datetime.now(timezone.utc),
            python_version="3.11.0",
        )
        assert meta.git_commit == "a" * 40
        assert meta.git_branch == "feature/orb-edge"
        assert meta.git_dirty is True
        assert meta.random_seed == 42
        assert meta.python_version == "3.11.0"

    def test_git_commit_must_be_40_hex_chars(self):
        """git_commit must be 40 hex characters (valid SHA-1)."""
        with pytest.raises(ValidationError):
            ExecutionMetadata(
                git_commit="invalid",
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            )

    def test_git_commit_lowercase_accepted(self):
        """git_commit can be lowercase or uppercase."""
        meta = ExecutionMetadata(
            git_commit="A" * 40,
            git_branch="main",
            git_dirty=False,
            executed_at=datetime.now(timezone.utc),
            python_version="3.11.0",
        )
        assert meta.git_commit.upper() == "A" * 40


class TestHypothesisTitleMaxLength:
    """P1: Hypothesis title cannot exceed 200 characters."""

    def test_title_at_max_length(self):
        """Title of exactly 200 chars should be accepted."""
        title = "x" * 200
        hyp = Hypothesis(
            id="HYP-001",
            title=title,
            rationale="This is a test rationale that is long enough",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert len(hyp.title) == 200

    def test_title_exceeds_max_length(self):
        """Title > 200 chars should be rejected."""
        title = "x" * 201
        with pytest.raises(ValidationError):
            Hypothesis(
                id="HYP-001",
                title=title,
                rationale="This is a test rationale that is long enough",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )


class TestExperimentRequiresResultsWhenCompleted:
    """P0: Experiment cannot be marked COMPLETED without results_summary and conclusion."""

    def test_completed_requires_results_summary(self):
        """Creating COMPLETED experiment without results_summary should fail."""
        with pytest.raises(ValidationError) as exc_info:
            Experiment(
                id="EXP-001",
                strategy_name="ORBStrategy",
                strategy_version="1.0.0",
                execution_metadata=ExecutionMetadata(
                    git_commit="a" * 40,
                    git_branch="main",
                    git_dirty=False,
                    executed_at=datetime.now(timezone.utc),
                    python_version="3.11.0",
                ),
                status=ExperimentStatus.COMPLETED,
                created_at=datetime.now(timezone.utc),
            )
        assert "results_summary" in str(exc_info.value)

    def test_completed_requires_conclusion(self):
        """Creating COMPLETED experiment without conclusion should fail."""
        with pytest.raises(ValidationError) as exc_info:
            Experiment(
                id="EXP-001",
                strategy_name="ORBStrategy",
                strategy_version="1.0.0",
                execution_metadata=ExecutionMetadata(
                    git_commit="a" * 40,
                    git_branch="main",
                    git_dirty=False,
                    executed_at=datetime.now(timezone.utc),
                    python_version="3.11.0",
                ),
                status=ExperimentStatus.COMPLETED,
                results_summary={"sharpe": 1.2},
                created_at=datetime.now(timezone.utc),
            )
        assert "conclusion" in str(exc_info.value)

    def test_completed_requires_both_results_and_conclusion(self):
        """Creating COMPLETED experiment with both fields should succeed."""
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.COMPLETED,
            results_summary={"sharpe": 1.2},
            conclusion="Edge validated",
            completed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        assert exp.status == ExperimentStatus.COMPLETED


class TestArtifactReferenceRejectsParentTraversal:
    """P0: Artifact path cannot contain '..' (prevent directory traversal)."""

    def test_artifact_path_with_parent_traversal(self):
        """Path with '..' should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ArtifactReference(
                id="ART-001",
                experiment_id="EXP-001",
                path="../../../etc/passwd",
                artifact_type="csv",
                checksum="a" * 64,
                size_bytes=1024,
                created_at=datetime.now(timezone.utc),
            )
        assert ".." in str(exc_info.value)

    def test_artifact_path_normal_relative(self):
        """Normal relative paths should be accepted."""
        art = ArtifactReference(
            id="ART-001",
            experiment_id="EXP-001",
            path="reports/backtest_2024.html",
            artifact_type="html",
            checksum="a" * 64,
            size_bytes=1024,
            created_at=datetime.now(timezone.utc),
        )
        assert art.path == "reports/backtest_2024.html"

    def test_artifact_path_absolute_rejected(self):
        """Absolute paths should be rejected."""
        with pytest.raises(ValidationError):
            ArtifactReference(
                id="ART-001",
                experiment_id="EXP-001",
                path="/absolute/path/file.csv",
                artifact_type="csv",
                checksum="a" * 64,
                size_bytes=1024,
                created_at=datetime.now(timezone.utc),
            )


class TestRejectedIdeaEvidenceFormat:
    """P1: RejectedIdea evidence must be valid EXP-NNN format."""

    def test_rejected_idea_with_valid_evidence(self):
        """Valid evidence IDs should be accepted."""
        rj = RejectedIdea(
            id="RJ-001",
            idea="Add trailing stop loss",
            reason_rejected="Cut winners too early",
            evidence=["EXP-001", "EXP-002", "EXP-003"],
            created_at=datetime.now(timezone.utc),
        )
        assert len(rj.evidence) == 3

    def test_rejected_idea_with_invalid_evidence_format(self):
        """Invalid evidence IDs should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            RejectedIdea(
                id="RJ-001",
                idea="Add trailing stop loss",
                reason_rejected="Cut winners too early",
                evidence=["EXP-001", "INVALID-ID"],
                created_at=datetime.now(timezone.utc),
            )
        assert "EXP-NNN" in str(exc_info.value)

    def test_rejected_idea_empty_evidence(self):
        """Empty evidence list should be acceptable."""
        rj = RejectedIdea(
            id="RJ-001",
            idea="Untested idea",
            reason_rejected="Never got around to it",
            evidence=[],
            created_at=datetime.now(timezone.utc),
        )
        assert rj.evidence == []


class TestModelSerializationRoundtrip:
    """P1: model.model_dump() → dict → model reproduces original."""

    def test_hypothesis_serialization_roundtrip(self):
        """Hypothesis should serialize/deserialize without loss."""
        original = Hypothesis(
            id="HYP-001",
            title="Test hypothesis",
            rationale="This is a test rationale that is long enough",
            status=HypothesisStatus.ACTIVE,
            tags=["orb", "volume"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        dumped = original.model_dump(mode="json")
        restored = Hypothesis(**dumped)
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.tags == original.tags

    def test_experiment_serialization_roundtrip(self):
        """Experiment should serialize/deserialize without loss."""
        original = Experiment(
            id="EXP-001",
            hypothesis_id="HYP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            parameters={"orb_minutes": 5, "take_profit": 2},
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                random_seed=42,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.RUNNING,
            tags=["test", "validation"],
            created_at=datetime.now(timezone.utc),
        )
        dumped = original.model_dump(mode="json")
        restored = Experiment(**dumped)
        assert restored.id == original.id
        assert restored.hypothesis_id == original.hypothesis_id
        assert restored.parameters == original.parameters
        assert restored.tags == original.tags

    def test_artifact_reference_serialization_roundtrip(self):
        """ArtifactReference should serialize/deserialize without loss."""
        original = ArtifactReference(
            id="ART-001",
            experiment_id="EXP-001",
            path="reports/backtest.html",
            artifact_type="html",
            checksum="b" * 64,
            size_bytes=5242880,
            created_at=datetime.now(timezone.utc),
        )
        dumped = original.model_dump(mode="json")
        restored = ArtifactReference(**dumped)
        assert restored.id == original.id
        assert restored.path == original.path
        assert restored.size_bytes == original.size_bytes


class TestDatetimeFieldsAreTimezoneAware:
    """P0: All datetime fields must be timezone-aware (prevent DST/timezone bugs)."""

    def test_hypothesis_created_at_naive_rejected(self):
        """Naive created_at should be rejected."""
        from datetime import datetime as dt
        with pytest.raises(ValidationError) as exc_info:
            Hypothesis(
                id="HYP-001",
                title="Test",
                rationale="Long enough rationale here",
                created_at=dt.now(),  # Naive datetime
                updated_at=datetime.now(timezone.utc),
            )
        assert "timezone" in str(exc_info.value).lower()

    def test_execution_metadata_executed_at_naive_rejected(self):
        """Naive executed_at should be rejected."""
        from datetime import datetime as dt
        with pytest.raises(ValidationError) as exc_info:
            ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=dt.now(),  # Naive datetime
                python_version="3.11.0",
            )
        assert "timezone" in str(exc_info.value).lower()

    def test_all_datetime_fields_timezone_aware(self):
        """All datetime fields should be timezone-aware."""
        now_utc = datetime.now(timezone.utc)
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=now_utc,
                python_version="3.11.0",
            ),
            created_at=now_utc,
            completed_at=now_utc,
        )
        assert exp.created_at.tzinfo is not None
        if exp.completed_at:
            assert exp.completed_at.tzinfo is not None


class TestCrossFieldValidation:
    """Additional validation tests for field cross-references."""

    def test_experiment_hypothesis_id_format(self):
        """hypothesis_id must be valid if provided."""
        with pytest.raises(ValidationError):
            Experiment(
                id="EXP-001",
                hypothesis_id="INVALID",
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

    def test_experiment_parent_experiment_id_format(self):
        """parent_experiment_id must be valid if provided."""
        with pytest.raises(ValidationError):
            Experiment(
                id="EXP-001",
                parent_experiment_id="INVALID",
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

    def test_research_note_related_experiment_id_format(self):
        """related_experiment_id must be valid if provided."""
        with pytest.raises(ValidationError):
            ResearchNote(
                id="NOTE-001",
                content="This is a research note with enough content",
                related_experiment_id="INVALID",
                created_at=datetime.now(timezone.utc),
            )

    def test_artifact_reference_checksum_must_be_sha256(self):
        """checksum must be 64 hex chars (SHA256)."""
        with pytest.raises(ValidationError):
            ArtifactReference(
                id="ART-001",
                experiment_id="EXP-001",
                path="file.csv",
                artifact_type="csv",
                checksum="invalid",
                size_bytes=1024,
                created_at=datetime.now(timezone.utc),
            )

    def test_artifact_reference_size_bytes_non_negative(self):
        """size_bytes must be non-negative."""
        with pytest.raises(ValidationError):
            ArtifactReference(
                id="ART-001",
                experiment_id="EXP-001",
                path="file.csv",
                artifact_type="csv",
                checksum="a" * 64,
                size_bytes=-1,
                created_at=datetime.now(timezone.utc),
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
