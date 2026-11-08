"""Explicit immutable study freeze and authorized final-holdout contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vibe.research_pipeline.hashing import hash_object
from vibe.research_pipeline.reliability import ReliabilityPolicy

FROZEN_STUDY_VERSION = 1
DEFAULT_FREEZE_AUTHORITY = "Bloom"


class FrozenStudyDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(FROZEN_STUDY_VERSION, frozen=True)
    strategy_id: str
    ruleset_identity: Mapping[str, Any]
    universe_identity: Mapping[str, Any]
    dataset_ids: tuple[str, ...]
    parameter_axes: tuple[Mapping[str, Any], ...]
    split_policy: Mapping[str, Any]
    warmup_policy: Mapping[str, Any]
    execution_model_version: int
    metric_calculation_version: int
    leakage_requirements: tuple[str, ...]
    reliability_policy: ReliabilityPolicy
    final_holdout_lock_hash: str
    frozen_at: datetime
    frozen_by: str = DEFAULT_FREEZE_AUTHORITY

    @field_validator("frozen_by")
    @classmethod
    def _authorized_researcher(cls, value: str) -> str:
        if value.strip() != DEFAULT_FREEZE_AUTHORITY:
            raise ValueError(
                f"Study freeze authority must be {DEFAULT_FREEZE_AUTHORITY!r}"
            )
        return DEFAULT_FREEZE_AUTHORITY

    @property
    def definition_hash(self) -> str:
        return hash_object(self)

    @classmethod
    def freeze(
        cls,
        *,
        frozen_by: str = DEFAULT_FREEZE_AUTHORITY,
        frozen_at: datetime | None = None,
        **definition: Any,
    ) -> "FrozenStudyDefinition":
        instant = frozen_at or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("frozen_at must be timezone-aware")
        return cls(frozen_at=instant, frozen_by=frozen_by, **definition)


class FinalHoldoutAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(..., min_length=16)
    requested_by: str = DEFAULT_FREEZE_AUTHORITY
    reason: str = Field(..., min_length=10)

    @field_validator("requested_by")
    @classmethod
    def _authorized_researcher(cls, value: str) -> str:
        if value.strip() != DEFAULT_FREEZE_AUTHORITY:
            raise ValueError(
                f"Final holdout authorization must be attributed to "
                f"{DEFAULT_FREEZE_AUTHORITY!r}"
            )
        return DEFAULT_FREEZE_AUTHORITY


class FinalHoldoutAuthorizationState(str, Enum):
    AUTHORIZED = "authorized"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class ClaimedFinalHoldoutAuthorization(BaseModel):
    """Durable identity proving the exactly-once claim happened before I/O."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    authorization_sha256: str = Field(..., min_length=64, max_length=64)
    claim_id: str
    requested_by: str = DEFAULT_FREEZE_AUTHORITY
    reason: str
    claimed_at: datetime

    @field_validator("requested_by")
    @classmethod
    def _authorized_researcher(cls, value: str) -> str:
        if value.strip() != DEFAULT_FREEZE_AUTHORITY:
            raise ValueError(
                f"Final holdout claim must be attributed to "
                f"{DEFAULT_FREEZE_AUTHORITY!r}"
            )
        return DEFAULT_FREEZE_AUTHORITY


class FinalHoldoutVerdict(str):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


def acceptance_verdict(outcome: Any) -> str:
    if not outcome.conclusive:
        return FinalHoldoutVerdict.INCONCLUSIVE
    return FinalHoldoutVerdict.PASSED if outcome.passed else FinalHoldoutVerdict.FAILED
