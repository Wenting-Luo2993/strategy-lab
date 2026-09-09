"""Run identity.

The existing sweep cache keys on ``base_ruleset_path.name`` -- the *filename* --
plus symbol, dates, swept parameters, capital, and slippage
(``vibe/backtester/analysis/parameter_sweep.py``). Nothing in that key covers
ruleset *content*, code version, feature definitions, or the execution model.
Editing a non-swept field of a ruleset therefore returns a cached pickle
computed under the old rules, silently and with no warning.

That is the single most dangerous defect in the current pipeline: it produces
results that look reproducible and are not. ``RunFingerprint`` replaces the
filename with the full set of inputs that can change an answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vibe.research_pipeline.hashing import (
    CANONICAL_ENCODING_VERSION,
    hash_file,
    hash_object,
)

__all__ = ["RunFingerprint", "FINGERPRINT_VERSION"]

# Bump when the set of fingerprinted inputs changes. Old fingerprints then
# compare unequal by construction, which is the correct behaviour: we cannot
# assert that a run predating a new input was computed the same way.
FINGERPRINT_VERSION = 1


class RunFingerprint(BaseModel):
    """Everything that can change a run's numbers, in one hashable record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprint_version: int = Field(default=FINGERPRINT_VERSION, ge=1)
    encoding_version: int = Field(default=CANONICAL_ENCODING_VERSION, ge=1)

    strategy_id: str = Field(..., min_length=1)

    ruleset_content_sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="Hash of ruleset file *contents*, never its name.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Fully resolved parameters, including defaults. Recording "
        "only the swept subset hides the rest of the configuration.",
    )

    universe_hash: str = Field(..., min_length=64, max_length=64)
    split_manifest_hash: str = Field(..., min_length=64, max_length=64)

    data_snapshot_id: str = Field(
        ...,
        min_length=1,
        description="Identifies the bar data used. Backfills and vendor "
        "restatements change results without any code change.",
    )

    feature_set_version: int = Field(..., ge=1)
    execution_model_version: int = Field(
        ...,
        ge=1,
        description="Bumped by any change to fill, slippage, cost, or intrabar "
        "exit-ordering semantics.",
    )
    metric_calculation_version: int = Field(..., ge=1)

    code_commit: str = Field(
        ...,
        min_length=7,
        description="Git commit of the code that produced the run.",
    )
    code_dirty: bool = Field(
        ...,
        description="True if the working tree had uncommitted changes. A dirty "
        "run is not reproducible and must never be promoted.",
    )

    random_seed: Optional[int] = None

    @field_validator(
        "ruleset_content_sha256", "universe_hash", "split_manifest_hash"
    )
    @classmethod
    def _is_hex_digest(cls, value: str) -> str:
        lowered = value.lower()
        if any(c not in "0123456789abcdef" for c in lowered):
            raise ValueError("Expected a lowercase hex SHA-256 digest")
        return lowered

    @field_validator("code_commit")
    @classmethod
    def _is_commit_like(cls, value: str) -> str:
        lowered = value.strip().lower()
        if any(c not in "0123456789abcdef" for c in lowered):
            raise ValueError(f"Expected a hex git commit hash, got {value!r}")
        return lowered

    @property
    def fingerprint(self) -> str:
        """Stable SHA-256 over every field above."""
        return hash_object(self)

    @property
    def is_reproducible(self) -> bool:
        """A dirty working tree cannot be reconstructed from the commit alone."""
        return not self.code_dirty

    def cache_key(self) -> str:
        """Key for any result cache.

        Identical to the fingerprint by design. Caches must not key on a
        narrower tuple than the one that determines the answer.
        """
        return self.fingerprint

    @staticmethod
    def hash_ruleset(path: str | Path) -> str:
        """Content hash of a ruleset file, for ``ruleset_content_sha256``."""
        return hash_file(Path(path))
