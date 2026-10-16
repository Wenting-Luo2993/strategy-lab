"""Record every segment a walk-forward evaluates, labelled with its true role.

``NestedWalkForwardDriver`` is deliberately pure: it takes an optimizer, a
selector, an executor and a validator, and performs no I/O. That is the right
shape -- but it meant the only walk-forward that produced genuine
out-of-sample evidence left no trace in the research store, while the legacy
in-sample one wrote rows and labelled them ``test``. Every row the store ever
held as out-of-sample came from the path that could not establish it.

Rather than push persistence into the driver, this decorator wraps the
``CandidateExecutor`` the driver was already given. Every evaluation the
walk-forward performs -- each training candidate, each validation candidate,
and the single frozen test run -- passes through the executor exactly once,
and :class:`SessionSegment` already carries its own
:class:`SegmentRole`. So the role is read off the thing being evaluated
instead of being asserted by the caller, which means a run cannot be filed
under a role it was not actually run on.

Recording never changes what the walk-forward returns. A store that is
unreachable, full, or mid-migration must not be able to alter a research
result; :class:`ResearchRunSink` already swallows and collects its own
failures, and this decorator returns the inner executor's result untouched
regardless.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional

from vibe.research_pipeline.contracts import SegmentRole, SessionSegment

logger = logging.getLogger(__name__)

__all__ = ["RecordingSegmentExecutor"]


class RecordingSegmentExecutor:
    """Wrap a ``CandidateExecutor`` so each evaluation lands in the store.

    Parameters
    ----------
    inner:
        The executor to delegate to. It must expose ``effective_ruleset`` so
        the recorded run pins the configuration that actually executed rather
        than the file it started from.
    sink:
        A :class:`ResearchRunSink`. Its failures are collected, not raised.
    record_roles:
        Which roles to persist. Defaults to every role, because a viewer that
        can only see test runs cannot show what the parameters were chosen
        *from*. Narrow it when a grid is large enough that recording every
        training evaluation would bury the folds it produced.
    note:
        Builds the run note from fold index, candidate and segment.
    """

    def __init__(
        self,
        inner: Any,
        sink: Any,
        *,
        record_roles: frozenset[SegmentRole] | None = None,
        note: Optional[Callable[[Any, SessionSegment], str]] = None,
        on_record: Optional[Callable[[Any, SessionSegment, Any], None]] = None,
    ) -> None:
        if not hasattr(inner, "effective_ruleset"):
            raise TypeError(
                f"{type(inner).__name__} cannot be recorded: it does not expose "
                "effective_ruleset(), so a recorded run could not pin the "
                "configuration that actually executed. Recording the base "
                "ruleset instead would file every swept point under identical "
                "settings."
            )
        self._inner = inner
        self._sink = sink
        self._roles = (
            frozenset(SegmentRole) if record_roles is None else frozenset(record_roles)
        )
        self._note = note or _default_note
        self._on_record = on_record
        self.skipped_roles: dict[str, int] = {}

    def __call__(self, candidate: Any, segment: SessionSegment) -> Any:
        result = self._inner(candidate, segment)
        role = getattr(segment, "role", None)
        if role is None or role not in self._roles:
            if role is not None:
                self.skipped_roles[role.value] = (
                    self.skipped_roles.get(role.value, 0) + 1
                )
            return result
        try:
            outcome = self._sink.record(
                _backtest_result(result),
                ruleset=self._inner.effective_ruleset(candidate),
                parameters=_parameters(candidate),
                segment_role=role,
                notes=self._note(candidate, segment),
            )
            if outcome is not None and self._on_record is not None:
                self._on_record(candidate, segment, outcome)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            logger.warning(
                "Recording a %s segment failed (%s: %s); the walk-forward "
                "result is unaffected.",
                role.value,
                type(exc).__name__,
                exc,
            )
        return result

    @property
    def effective_ruleset(self) -> Any:
        return self._inner.effective_ruleset


def _backtest_result(result: Any) -> Any:
    """Unwrap the ``BacktestResult`` the sink grades.

    ``SegmentResult`` carries warmup provenance around a ``BacktestResult``;
    the sink wants the latter. Accepting both keeps this usable with executors
    that return a bare result.
    """
    return getattr(result, "result", result)


def _parameters(candidate: Any) -> Mapping[str, Any]:
    getter = getattr(candidate, "parameter_map", None)
    if getter is None:
        raise TypeError(
            f"{type(candidate).__name__} has no parameter_map, so the recorded "
            "run could not say which point of the grid it was."
        )
    return dict(getter)


def _default_note(candidate: Any, segment: SessionSegment) -> str:
    candidate_id = getattr(candidate, "candidate_id", "?")
    return (
        f"Nested walk-forward {segment.role.value} segment "
        f"{segment.start_session}..{segment.end_session} "
        f"(candidate {candidate_id})"
    )
