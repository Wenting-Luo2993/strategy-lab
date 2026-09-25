"""Storage seam for research records.

Declaring this protocol in P0 lets the SQLite implementation (P7), the registry
migration (P11), and the Supabase publisher (P12) proceed independently against
a fixed interface.

Two invariants are part of the contract, not the implementation:

1. **Append-only.** Terminal runs are never mutated. Corrections are new rows
   that supersede old ones, so the audit trail survives.
2. **No trades or equity leave the machine.** ``publish`` implementations must
   refuse trade and equity payloads by construction, not by convention.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional, Protocol, runtime_checkable

from vibe.research_pipeline.contracts import RunEvidence, ValidationFinding
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.validation import ValidationReport

__all__ = [
    "ResearchStore",
    "RunRecord",
    "StoreError",
    "ImmutableRecordError",
    "LeaseError",
    "ValidationRequiredError",
]


class StoreError(RuntimeError):
    """Base class for storage failures."""


class ImmutableRecordError(StoreError):
    """Raised on an attempt to mutate a terminal run record."""


class LeaseError(StoreError):
    """Raised when a run lease is missing, expired, or owned by another worker."""


class ValidationRequiredError(StoreError):
    """Raised when a caller tries to bypass the durable validation gate."""


class RunRecord(Protocol):
    """Minimal read shape returned by queries."""

    run_id: str
    state: RunState
    fingerprint: str
    methodology_version: str


@runtime_checkable
class ResearchStore(Protocol):
    """Persistence interface for research runs."""

    def register_run(
        self,
        *,
        fingerprint: RunFingerprint,
        methodology_version: str,
        notes: Optional[str] = None,
    ) -> str:
        """Create a run in ``REGISTERED`` and return its id.

        Registering the same fingerprint twice must return the existing id
        rather than creating a duplicate.
        """

    def transition(
        self,
        run_id: str,
        *,
        target: RunState,
        reason: Optional[str] = None,
        lease_token: Optional[str] = None,
    ) -> None:
        """Move a run along a legal edge, recording the transition.

        A transition out of ``RUNNING`` requires the current lease token.
        Raises :class:`ImmutableRecordError` if the run is already terminal, and
        ``IllegalTransitionError`` if the edge is not permitted.
        """

    def start_run(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> str:
        """Atomically enter ``RUNNING`` with a lease and return its token."""

    def heartbeat(
        self,
        run_id: str,
        *,
        lease_token: str,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> None:
        """Extend a live lease owned by ``lease_token``."""

    def sweep_stale_leases(self, *, now: Optional[datetime] = None) -> list[str]:
        """Mark expired ``RUNNING`` rows ``EXECUTION_FAILED``."""

    def finalize_validation(self, run_id: str, report: ValidationReport) -> None:
        """Persist a complete report and atomically apply its terminal state."""

    def record_metrics(
        self, run_id: str, metrics: dict[str, float], *, calculation_version: int
    ) -> None:
        """Attach computed metrics to a non-terminal run."""

    def record_evidence(self, run_id: str, evidence: RunEvidence) -> None:
        """Attach the tamper-evident evidence record.

        Required under every retention profile, including ``summary``.
        """

    def record_findings(
        self, run_id: str, findings: Iterable[ValidationFinding]
    ) -> None:
        """Attach validation findings from a single validation pass."""

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Fetch one run, or None."""

    def find_by_fingerprint(self, fingerprint: str) -> Optional[RunRecord]:
        """Look up an existing run by its identity hash."""

    def query_runs(
        self,
        *,
        state: Optional[RunState] = None,
        strategy_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Paginated run query."""

    def enqueue_publication(self, run_id: str, payload: dict[str, Any]) -> None:
        """Queue a summary payload for remote publication.

        Implementations must reject payloads containing trade or equity data.
        """
