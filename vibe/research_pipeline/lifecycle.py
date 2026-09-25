"""Run lifecycle states and legal transitions.

The central rule this module enforces: **a run cannot reach ``COMPLETED``
without passing validation.** In the previous YAML-based workflow, a run that
produced impossible metrics still wrote a ``completed`` record indistinguishable
from a good one. Encoding the state machine here makes that structurally
impossible rather than a matter of discipline.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "RunState",
    "TERMINAL_STATES",
    "LEGAL_TRANSITIONS",
    "IllegalTransitionError",
    "is_legal_transition",
    "assert_legal_transition",
    "is_terminal",
]


class RunState(str, Enum):
    """Lifecycle states for a single research run."""

    REGISTERED = "registered"
    """Run identity and fingerprint recorded; execution not started."""

    RUNNING = "running"
    """Execution in progress; a lease is held."""

    VALIDATING = "validating"
    """Execution finished; metric, split, and look-ahead gates are running."""

    COMPLETED = "completed"
    """Executed and passed every blocking gate. The only trustworthy state."""

    REVIEW_REQUIRED = "review_required"
    """Executed, no hard invariant broken, but a plausibility gate flagged the
    result. Blocks promotion until a human records a resolution."""

    VALIDATION_FAILED = "validation_failed"
    """A hard invariant or accounting reconciliation failed. The numbers are
    known-wrong; the record is retained as evidence, never as a result."""

    EXECUTION_FAILED = "execution_failed"
    """The run crashed, or its lease expired and was swept. Distinct from
    ``VALIDATION_FAILED``: we have no metrics at all, rather than bad ones."""

    INCONCLUSIVE = "inconclusive"
    """Ran cleanly but cannot support a conclusion, e.g. zero trades, or a
    walk-forward in which at least one fold failed. Explicitly not a result."""

    ARCHIVED = "archived"
    """Superseded or retired. Retained for lineage."""


TERMINAL_STATES: frozenset[RunState] = frozenset(
    {
        RunState.COMPLETED,
        RunState.VALIDATION_FAILED,
        RunState.EXECUTION_FAILED,
        RunState.INCONCLUSIVE,
        RunState.ARCHIVED,
    }
)

# Note that COMPLETED is reachable only from VALIDATING. There is deliberately
# no RUNNING -> COMPLETED edge: skipping validation is not expressible.
LEGAL_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.REGISTERED: frozenset(
        {RunState.RUNNING, RunState.EXECUTION_FAILED, RunState.ARCHIVED}
    ),
    RunState.RUNNING: frozenset({RunState.VALIDATING, RunState.EXECUTION_FAILED}),
    RunState.VALIDATING: frozenset(
        {
            RunState.COMPLETED,
            RunState.REVIEW_REQUIRED,
            RunState.VALIDATION_FAILED,
            RunState.INCONCLUSIVE,
            RunState.EXECUTION_FAILED,
        }
    ),
    # A reviewer resolves a flagged run either way, but cannot invent a pass
    # for something that never ran validation.
    RunState.REVIEW_REQUIRED: frozenset(
        {
            RunState.COMPLETED,
            RunState.VALIDATION_FAILED,
            RunState.INCONCLUSIVE,
            RunState.ARCHIVED,
        }
    ),
    RunState.COMPLETED: frozenset({RunState.ARCHIVED}),
    RunState.VALIDATION_FAILED: frozenset({RunState.ARCHIVED}),
    RunState.EXECUTION_FAILED: frozenset({RunState.ARCHIVED}),
    RunState.INCONCLUSIVE: frozenset({RunState.ARCHIVED}),
    RunState.ARCHIVED: frozenset(),
}


class IllegalTransitionError(ValueError):
    """Raised when a run is moved between states along a forbidden edge."""


def is_legal_transition(source: RunState, target: RunState) -> bool:
    """Return True if ``source -> target`` is permitted."""
    return target in LEGAL_TRANSITIONS[source]


def assert_legal_transition(source: RunState, target: RunState) -> None:
    """Raise :class:`IllegalTransitionError` unless the transition is legal."""
    if not is_legal_transition(source, target):
        allowed = sorted(s.value for s in LEGAL_TRANSITIONS[source])
        raise IllegalTransitionError(
            f"Illegal run transition {source.value!r} -> {target.value!r}. "
            f"Allowed from {source.value!r}: {allowed or ['<terminal>']}."
        )


def is_terminal(state: RunState) -> bool:
    """Return True if no further transition except archival is meaningful."""
    return state in TERMINAL_STATES
