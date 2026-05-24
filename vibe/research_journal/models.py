"""Domain models for Research Journal / Experiment Registry Framework.

Defines core entities with validation and immutability semantics:
- Hypothesis: Research question being tested
- Experiment: Specific test execution with results
- ResearchNote: Observations and insights
- RejectedIdea: Failed hypotheses with evidence
- ArtifactReference: Links to backtest results, outputs
- ExecutionMetadata: Git state, reproducibility info
"""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HypothesisStatus(str, Enum):
    """Hypothesis lifecycle states."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


class ExperimentStatus(str, Enum):
    """Experiment lifecycle states."""

    REGISTERED = "registered"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"


class ExecutionMetadata(BaseModel):
    """Metadata for experiment execution reproducibility.

    Captures the exact conditions under which an experiment was run:
    - Code version (git commit/branch)
    - Random seed for determinism
    - Python version
    - Execution timestamp
    """

    model_config = ConfigDict(frozen=True)

    git_commit: str = Field(..., description="Full commit hash (40 hex chars)")
    git_branch: str = Field(..., description="Branch name at execution time")
    git_dirty: bool = Field(
        ..., description="True if uncommitted changes present at execution"
    )
    random_seed: Optional[int] = Field(
        None, description="Random seed for reproducibility (optional)"
    )
    executed_at: datetime = Field(..., description="UTC timestamp of execution")
    python_version: str = Field(..., description="Python version string")

    @field_validator("git_commit")
    @classmethod
    def validate_git_commit(cls, v: str) -> str:
        """Validate git commit is 40 hex characters."""
        if not re.match(r"^[0-9a-f]{40}$", v.lower()):
            raise ValueError("git_commit must be 40 hex characters")
        return v

    @field_validator("executed_at")
    @classmethod
    def validate_executed_at_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure executed_at is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("executed_at must be timezone-aware (use UTC)")
        return v


class Hypothesis(BaseModel):
    """Research hypothesis to be tested.

    Represents a scientific question with rationale and tracking.
    Can be in various lifecycle states from PROPOSED to VALIDATED/INVALIDATED.
    """

    model_config = ConfigDict(frozen=False)

    id: str = Field(..., description="Unique ID in format HYP-NNN (e.g., HYP-001)")
    title: str = Field(..., max_length=200, description="Concise hypothesis statement")
    rationale: str = Field(
        ..., min_length=10, description="Why we believe this hypothesis"
    )
    status: HypothesisStatus = Field(default=HypothesisStatus.PROPOSED)
    tags: List[str] = Field(default_factory=list, description="Categorization tags")
    created_at: datetime = Field(..., description="UTC timestamp of creation")
    updated_at: datetime = Field(..., description="UTC timestamp of last update")

    @field_validator("id")
    @classmethod
    def validate_hypothesis_id(cls, v: str) -> str:
        """Validate hypothesis ID format HYP-NNN where NNN is 3+ digits."""
        if not re.match(r"^HYP-\d{3,}$", v):
            raise ValueError("Hypothesis ID must match format HYP-NNN (3+ digits)")
        return v

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_datetime_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure all datetime fields are timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("All datetime fields must be timezone-aware (use UTC)")
        return v


class Experiment(BaseModel):
    """Research experiment with configuration, execution, and results.

    Represents a specific test run of a strategy with particular parameters
    against a specific dataset. Once COMPLETED or FAILED, becomes immutable
    for scientific integrity.
    """

    model_config = ConfigDict(frozen=False)

    id: str = Field(..., description="Unique ID in format EXP-NNN (e.g., EXP-001)")
    hypothesis_id: Optional[str] = Field(
        None, description="Link to parent hypothesis (format HYP-NNN)"
    )
    parent_experiment_id: Optional[str] = Field(
        None, description="Link to parent experiment for lineage tracking (format EXP-NNN)"
    )
    strategy_name: str = Field(..., description="Name of strategy tested (e.g., ORBStrategy)")
    strategy_version: str = Field(..., description="Version of strategy (e.g., 1.4.2)")
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Strategy configuration parameters"
    )
    dataset_config: Dict[str, Any] = Field(
        default_factory=dict, description="Dataset specification (symbols, date range, etc.)"
    )
    execution_metadata: ExecutionMetadata = Field(
        ..., description="Git state and reproducibility info"
    )
    status: ExperimentStatus = Field(default=ExperimentStatus.REGISTERED)
    results_summary: Optional[Dict[str, Any]] = Field(
        None, description="Metrics and summary results (Sharpe, expectancy, P&L, etc.)"
    )
    conclusion: Optional[str] = Field(None, description="Human conclusion from results")
    artifacts: List[str] = Field(
        default_factory=list, description="List of artifact IDs (ART-NNN format)"
    )
    tags: List[str] = Field(default_factory=list, description="Categorization tags")
    created_at: datetime = Field(..., description="UTC timestamp of creation")
    completed_at: Optional[datetime] = Field(None, description="UTC timestamp of completion")

    def model_post_init(self, __context):
        """Initialize internal state after model construction."""
        object.__setattr__(self, "_completing", False)

    @field_validator("id")
    @classmethod
    def validate_experiment_id(cls, v: str) -> str:
        """Validate experiment ID format EXP-NNN where NNN is 3+ digits."""
        if not re.match(r"^EXP-\d{3,}$", v):
            raise ValueError("Experiment ID must match format EXP-NNN (3+ digits)")
        return v

    @field_validator("hypothesis_id")
    @classmethod
    def validate_hypothesis_id_format(cls, v: Optional[str]) -> Optional[str]:
        """Validate hypothesis_id format if provided."""
        if v is not None and not re.match(r"^HYP-\d{3,}$", v):
            raise ValueError("hypothesis_id must match format HYP-NNN (3+ digits)")
        return v

    @field_validator("parent_experiment_id")
    @classmethod
    def validate_parent_experiment_id_format(cls, v: Optional[str]) -> Optional[str]:
        """Validate parent_experiment_id format if provided."""
        if v is not None and not re.match(r"^EXP-\d{3,}$", v):
            raise ValueError("parent_experiment_id must match format EXP-NNN (3+ digits)")
        return v

    @field_validator("created_at", "completed_at")
    @classmethod
    def validate_datetime_timezone_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure all datetime fields are timezone-aware."""
        if v is not None and v.tzinfo is None:
            raise ValueError("All datetime fields must be timezone-aware (use UTC)")
        return v

    @model_validator(mode="after")
    def validate_completed_requires_results(self) -> "Experiment":
        """If status is COMPLETED, results_summary and conclusion must be set."""
        if self.status == ExperimentStatus.COMPLETED:
            if self.results_summary is None:
                raise ValueError(
                    "results_summary must be set before marking experiment as COMPLETED"
                )
            if self.conclusion is None:
                raise ValueError(
                    "conclusion must be set before marking experiment as COMPLETED"
                )
        return self

    def is_immutable(self) -> bool:
        """Check if experiment is immutable (completed or failed)."""
        return self.status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED)

    def mark_completed(self, results: Dict[str, Any], conclusion: str) -> None:
        """Mark experiment as completed with results and conclusion.

        Args:
            results: Dictionary of result metrics and summary statistics
            conclusion: Human-readable conclusion from the experiment

        Raises:
            ValueError: If experiment is already in a final state
        """
        if self.is_immutable():
            raise ValueError(
                f"Cannot modify experiment in status {self.status}; already in final state"
            )

        # Temporarily allow modifications during completion
        object.__setattr__(self, "_completing", True)
        try:
            self.status = ExperimentStatus.COMPLETED
            self.results_summary = results
            self.conclusion = conclusion
            self.completed_at = datetime.now(datetime.now().astimezone().tzinfo)
        finally:
            object.__setattr__(self, "_completing", False)

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent modification of immutable experiments (except during mark_completed)."""
        # Allow modifications during mark_completed() call
        if getattr(self, "_completing", False):
            return super().__setattr__(name, value)

        # Check if already immutable
        if getattr(self, "is_immutable", lambda: False)():
            raise ValueError(
                f"Cannot modify {name}: experiment is immutable (status={self.status})"
            )
        super().__setattr__(name, value)


class ResearchNote(BaseModel):
    """Freeform observation or insight during research.

    Captures important findings, surprising discoveries, regime observations,
    parameter sensitivities, or other insights that don't fit into
    structured hypothesis/experiment model.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique ID in format NOTE-NNN (e.g., NOTE-001)")
    content: str = Field(
        ..., min_length=10, description="Freeform observation text"
    )
    related_experiment_id: Optional[str] = Field(
        None, description="Link to related experiment (format EXP-NNN)"
    )
    tags: List[str] = Field(default_factory=list, description="Categorization tags")
    created_at: datetime = Field(..., description="UTC timestamp of creation")

    @field_validator("id")
    @classmethod
    def validate_note_id(cls, v: str) -> str:
        """Validate note ID format NOTE-NNN where NNN is 3+ digits."""
        if not re.match(r"^NOTE-\d{3,}$", v):
            raise ValueError("Note ID must match format NOTE-NNN (3+ digits)")
        return v

    @field_validator("related_experiment_id")
    @classmethod
    def validate_related_experiment_id_format(cls, v: Optional[str]) -> Optional[str]:
        """Validate related_experiment_id format if provided."""
        if v is not None and not re.match(r"^EXP-\d{3,}$", v):
            raise ValueError("related_experiment_id must match format EXP-NNN (3+ digits)")
        return v

    @field_validator("created_at")
    @classmethod
    def validate_datetime_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure created_at is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (use UTC)")
        return v


class RejectedIdea(BaseModel):
    """Record of a hypothesis or idea that was tested and rejected.

    Preserves knowledge of what has been tried and failed,
    preventing repeated mistakes and wasted research effort.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique ID in format RJ-NNN (e.g., RJ-001)")
    idea: str = Field(..., description="Description of what was tested")
    reason_rejected: str = Field(..., description="Why it failed and conclusions")
    evidence: List[str] = Field(
        default_factory=list, description="Experiment IDs that invalidated it (EXP-NNN format)"
    )
    tags: List[str] = Field(default_factory=list, description="Categorization tags")
    created_at: datetime = Field(..., description="UTC timestamp of creation")

    @field_validator("id")
    @classmethod
    def validate_rejected_idea_id(cls, v: str) -> str:
        """Validate rejected idea ID format RJ-NNN where NNN is 3+ digits."""
        if not re.match(r"^RJ-\d{3,}$", v):
            raise ValueError("Rejected Idea ID must match format RJ-NNN (3+ digits)")
        return v

    @field_validator("evidence")
    @classmethod
    def validate_evidence_ids(cls, v: List[str]) -> List[str]:
        """Validate all evidence IDs are in EXP-NNN format."""
        for exp_id in v:
            if not re.match(r"^EXP-\d{3,}$", exp_id):
                raise ValueError(
                    f"Evidence must contain valid experiment IDs (EXP-NNN format), got {exp_id}"
                )
        return v

    @field_validator("created_at")
    @classmethod
    def validate_datetime_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure created_at is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (use UTC)")
        return v


class ArtifactReference(BaseModel):
    """Reference to a research artifact (backtest results, output file, etc.).

    Links experiments to their output artifacts (HTML reports, CSVs, images, etc.)
    with checksums for integrity verification and storage path information.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique ID in format ART-NNN (e.g., ART-001)")
    experiment_id: str = Field(
        ..., description="ID of related experiment (format EXP-NNN)"
    )
    path: str = Field(
        ..., description="Relative path from repo root (must not contain '..')"
    )
    artifact_type: str = Field(
        ..., description="Type of artifact (parquet|html|csv|image|json|markdown)"
    )
    checksum: str = Field(
        ..., description="SHA256 hash of artifact (64 hex characters)"
    )
    size_bytes: int = Field(..., description="Size in bytes")
    created_at: datetime = Field(..., description="UTC timestamp of creation")

    @field_validator("id")
    @classmethod
    def validate_artifact_id(cls, v: str) -> str:
        """Validate artifact ID format ART-NNN where NNN is 3+ digits."""
        if not re.match(r"^ART-\d{3,}$", v):
            raise ValueError("Artifact ID must match format ART-NNN (3+ digits)")
        return v

    @field_validator("experiment_id")
    @classmethod
    def validate_experiment_id_format(cls, v: str) -> str:
        """Validate experiment_id format."""
        if not re.match(r"^EXP-\d{3,}$", v):
            raise ValueError("experiment_id must match format EXP-NNN (3+ digits)")
        return v

    @field_validator("path")
    @classmethod
    def validate_path_no_traversal(cls, v: str) -> str:
        """Prevent directory traversal attacks in artifact paths."""
        if ".." in v:
            raise ValueError("Artifact path cannot contain '..' (directory traversal)")
        if v.startswith("/") or (len(v) > 1 and v[1] == ":"):
            raise ValueError("Artifact path must be relative (not absolute)")
        return v

    @field_validator("checksum")
    @classmethod
    def validate_sha256_checksum(cls, v: str) -> str:
        """Validate checksum is valid SHA256 (64 hex characters)."""
        if not re.match(r"^[0-9a-f]{64}$", v.lower()):
            raise ValueError("checksum must be valid SHA256 hash (64 hex characters)")
        return v

    @field_validator("size_bytes")
    @classmethod
    def validate_size_bytes(cls, v: int) -> int:
        """Validate size is non-negative."""
        if v < 0:
            raise ValueError("size_bytes must be non-negative")
        return v

    @field_validator("created_at")
    @classmethod
    def validate_datetime_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure created_at is timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (use UTC)")
        return v
