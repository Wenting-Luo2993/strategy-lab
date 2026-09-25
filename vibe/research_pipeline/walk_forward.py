"""Nested walk-forward fold driver and survivorship-safe OOS stitching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from vibe.common.models.trade import Trade
from vibe.research_pipeline.contracts import SegmentRole, SessionSegment
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.optimization import (
    CandidateFailure,
    CandidateExecutor,
    CandidateSpec,
    CandidateValidator,
    OptimizationBatch,
    Optimizer,
    SelectionIncompleteError,
    SelectionResult,
    Selector,
    ValidationEvaluation,
)
from vibe.research_pipeline.splits.calendar import SessionCalendar
from vibe.research_pipeline.splits.planner import SplitPlan
from vibe.research_pipeline.validation import ValidationReport, ValidationScope

__all__ = [
    "FoldResult",
    "NestedWalkForwardDriver",
    "NestedWalkForwardResult",
    "SessionContaminationError",
    "StitchedOOSMetrics",
    "audit_session_separation",
]


class SessionContaminationError(ValueError):
    pass


def audit_session_separation(
    optimizer_sessions: Iterable,
    selector_sessions: Iterable,
    test_sessions: Iterable,
) -> None:
    """Reject the first exact session reused across graded roles (F9)."""
    groups = (
        ("optimizer", set(optimizer_sessions)),
        ("selector", set(selector_sessions)),
        ("test", set(test_sessions)),
    )
    for index, (left_name, left) in enumerate(groups):
        for right_name, right in groups[index + 1 :]:
            overlap = sorted(left & right)
            if overlap:
                raise SessionContaminationError(
                    f"{left_name} and {right_name} overlap on session {overlap[0]}"
                )


@dataclass(frozen=True)
class FoldResult:
    fold_index: int
    manifest_hash: str
    optimization: OptimizationBatch | None
    selection: SelectionResult | None
    validation_evaluations: tuple[ValidationEvaluation, ...]
    validation_failures: tuple[CandidateFailure, ...]
    test_result: object | None
    test_validation: ValidationReport | None
    state: RunState
    reason: str | None = None


@dataclass(frozen=True)
class StitchedOOSMetrics:
    n_folds: int
    n_trades: int
    expectancy_r: float
    fold_net_pnl: tuple[float, ...]
    first_session: object | None
    last_session: object | None
    trades: tuple[Trade, ...]


@dataclass(frozen=True)
class NestedWalkForwardResult:
    plan_hash: str
    folds: tuple[FoldResult, ...]
    n_folds_expected: int
    n_folds_completed: int
    state: RunState
    stitched_oos: StitchedOOSMetrics | None
    reason: str | None = None


class NestedWalkForwardDriver:
    def __init__(
        self,
        optimizer: Optimizer,
        selector: Selector,
        executor: CandidateExecutor,
        validator: CandidateValidator,
        *,
        calendar: SessionCalendar | None = None,
    ) -> None:
        self._optimizer = optimizer
        self._selector = selector
        self._executor = executor
        self._validator = validator
        self._calendar = calendar

    def run(
        self,
        plan: SplitPlan,
        candidates: Sequence[CandidateSpec],
    ) -> NestedWalkForwardResult:
        folds: list[FoldResult] = []
        for fold_index, manifest in enumerate(plan.folds):
            train = self._one(manifest.segments, SegmentRole.TRAIN)
            validation = self._one(manifest.segments, SegmentRole.VALIDATION)
            test = self._one(manifest.segments, SegmentRole.TEST)
            calendar = self._calendar or SessionCalendar(manifest.calendar_name)
            audit_session_separation(
                calendar.sessions_between(train.start_session, train.end_session),
                calendar.sessions_between(
                    validation.start_session, validation.end_session
                ),
                calendar.sessions_between(test.start_session, test.end_session),
            )

            optimization = self._optimizer.fit(train, candidates)
            try:
                selection = self._selector.select(optimization, validation)
            except SelectionIncompleteError as exc:
                folds.append(
                    FoldResult(
                        fold_index,
                        manifest.manifest_hash,
                        optimization,
                        None,
                        exc.evaluations,
                        exc.failures,
                        None,
                        None,
                        RunState.INCONCLUSIVE,
                        str(exc),
                    )
                )
                return self._inconclusive(plan, folds, str(exc))

            try:
                test_result = self._executor(selection.selected.candidate, test)
                report = self._validator(
                    selection.selected.candidate,
                    test_result,
                    ValidationScope.CANDIDATE,
                )
            except Exception as exc:
                reason = f"TEST execution failed: {type(exc).__name__}: {exc}"
                folds.append(
                    FoldResult(
                        fold_index,
                        manifest.manifest_hash,
                        optimization,
                        selection,
                        selection.evaluations,
                        (),
                        None,
                        None,
                        RunState.EXECUTION_FAILED,
                        reason,
                    )
                )
                return self._inconclusive(plan, folds, reason)

            folds.append(
                FoldResult(
                    fold_index=fold_index,
                    manifest_hash=manifest.manifest_hash,
                    optimization=optimization,
                    selection=selection,
                    validation_evaluations=selection.evaluations,
                    validation_failures=(),
                    test_result=test_result,
                    test_validation=report,
                    state=report.target_state,
                    reason=None
                    if report.target_state is RunState.COMPLETED
                    else f"TEST validation ended {report.target_state.value}",
                )
            )
            if report.target_state is not RunState.COMPLETED:
                return self._inconclusive(plan, folds, folds[-1].reason or "")

        if len(folds) != len(plan.folds):
            return self._inconclusive(
                plan, folds, "Not every expected fold completed"
            )
        stitched = self._stitch(folds, plan)
        return NestedWalkForwardResult(
            plan_hash=plan.plan_hash,
            folds=tuple(folds),
            n_folds_expected=len(plan.folds),
            n_folds_completed=len(folds),
            state=RunState.COMPLETED,
            stitched_oos=stitched,
        )

    @staticmethod
    def _one(
        segments: Sequence[SessionSegment], role: SegmentRole
    ) -> SessionSegment:
        matches = [segment for segment in segments if segment.role is role]
        if len(matches) != 1:
            raise ValueError(
                f"Fold requires exactly one {role.value} segment, got {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _inconclusive(
        plan: SplitPlan, folds: Sequence[FoldResult], reason: str
    ) -> NestedWalkForwardResult:
        return NestedWalkForwardResult(
            plan_hash=plan.plan_hash,
            folds=tuple(folds),
            n_folds_expected=len(plan.folds),
            n_folds_completed=sum(
                fold.state is RunState.COMPLETED for fold in folds
            ),
            state=RunState.INCONCLUSIVE,
            stitched_oos=None,
            reason=reason,
        )

    @staticmethod
    def _stitch(
        folds: Sequence[FoldResult], plan: SplitPlan
    ) -> StitchedOOSMetrics:
        if len(folds) != len(plan.folds) or any(
            fold.state is not RunState.COMPLETED for fold in folds
        ):
            raise ValueError("Cannot stitch incomplete or non-completed folds")

        trades: list[Trade] = []
        previous_end = None
        for fold, manifest in zip(folds, plan.folds):
            test = NestedWalkForwardDriver._one(
                manifest.segments, SegmentRole.TEST
            )
            if previous_end is not None and test.start_session <= previous_end:
                raise ValueError(
                    "Cannot stitch overlapping or out-of-order TEST windows: "
                    f"{test.start_session} <= {previous_end}"
                )
            previous_end = test.end_session
            trades.extend(fold.test_result.result.trades)

        trades.sort(key=lambda trade: trade.entry_time)
        r_values = [
            (trade.pnl - (trade.commission or 0.0)) / trade.initial_risk
            for trade in trades
            if trade.initial_risk and trade.initial_risk > 0
        ]
        fold_net_pnl = tuple(
            sum(
                trade.pnl - (trade.commission or 0.0)
                for trade in fold.test_result.result.trades
            )
            for fold in folds
        )
        return StitchedOOSMetrics(
            n_folds=len(folds),
            n_trades=len(trades),
            expectancy_r=sum(r_values) / len(r_values) if r_values else 0.0,
            fold_net_pnl=fold_net_pnl,
            first_session=trades[0].entry_time.date() if trades else None,
            last_session=trades[-1].entry_time.date() if trades else None,
            trades=tuple(trades),
        )
