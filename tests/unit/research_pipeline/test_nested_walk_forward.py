from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from vibe.research_pipeline.contracts import (
    SegmentRole,
    Severity,
    ValidationCategory,
    ValidationFinding,
)
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.optimization import (
    Optimizer,
    ParameterAxis,
    Selector,
    generate_grid,
)
from vibe.research_pipeline.splits.planner import (
    SplitSpec,
    TemporalSplitPlanner,
)
from vibe.research_pipeline.validation import ValidationReport
from vibe.research_pipeline.walk_forward import (
    NestedWalkForwardDriver,
    SessionContaminationError,
    audit_session_separation,
)


def _report(scope, state=RunState.COMPLETED):
    findings = ()
    if state is RunState.INCONCLUSIVE:
        findings = (
            ValidationFinding(
                code="SUFF-TEST",
                category=ValidationCategory.DATASET_SUFFICIENCY,
                severity=Severity.ADVISORY,
                message="Synthetic insufficient sample",
            ),
        )
    elif state is RunState.REVIEW_REQUIRED:
        findings = (
            ValidationFinding(
                code="PLAUS-TEST",
                category=ValidationCategory.PLAUSIBILITY,
                severity=Severity.REVIEW,
                message="Synthetic plausibility review",
            ),
        )
    elif state is RunState.VALIDATION_FAILED:
        findings = (
            ValidationFinding(
                code="MATH-TEST",
                category=ValidationCategory.HARD_INVARIANT,
                severity=Severity.BLOCKING,
                message="Synthetic blocking failure",
            ),
        )
    return ValidationReport(
        profile_name="test",
        scope=scope,
        target_state=state,
        findings=findings,
        checked_categories=(),
        registry_hash_at_execution="a" * 64,
        current_registry_hash="a" * 64,
        leakage_passed=True,
    )


def _plan():
    return TemporalSplitPlanner().plan(
        SplitSpec(
            dev_start=date(2020, 1, 2),
            dev_end=date(2020, 3, 31),
            final_oos_start=date(2020, 4, 1),
            final_oos_end=date(2020, 4, 30),
            train_sessions=10,
            validation_sessions=5,
            test_sessions=5,
            step_sessions=20,
            purge_padding_sessions=0,
            embargo_padding_sessions=0,
        )
    )


def _driver(test_state=RunState.COMPLETED, fail_role=None):
    calls = []

    def execute(candidate, segment):
        calls.append((candidate.candidate_id, segment.role))
        if segment.role is fail_role:
            raise RuntimeError("deliberate fold failure")
        score = float(candidate.parameter_map["p"])
        trade = SimpleNamespace(
            entry_time=datetime.combine(segment.start_session, datetime.min.time()),
            pnl=score,
            commission=0.0,
            initial_risk=1.0,
        )
        return SimpleNamespace(
            segment=segment,
            result=SimpleNamespace(score=score, trades=[trade]),
        )

    def validate(candidate, result, scope):
        state = test_state if result.segment.role is SegmentRole.TEST else RunState.COMPLETED
        return _report(scope, state)

    score = lambda result: result.score
    optimizer = Optimizer(execute, validate, score)
    selector = Selector(execute, validate, score, plateau_tolerance=0.0)
    return NestedWalkForwardDriver(optimizer, selector, execute, validate), calls


def test_f9_names_exact_overlapping_session():
    with pytest.raises(SessionContaminationError, match="2020-01-03"):
        audit_session_separation(
            {date(2020, 1, 2), date(2020, 1, 3)},
            {date(2020, 1, 6)},
            {date(2020, 1, 3)},
        )


def test_nested_driver_keeps_roles_separate_and_stitches_every_fold():
    plan = _plan()
    candidates = generate_grid((ParameterAxis("p", (1, 2)),))
    driver, calls = _driver()

    result = driver.run(plan, candidates)

    assert result.state is RunState.COMPLETED
    assert result.n_folds_completed == result.n_folds_expected
    assert result.stitched_oos is not None
    assert result.stitched_oos.n_folds == len(plan.folds)
    assert result.stitched_oos.n_trades == len(plan.folds)
    assert all(fold.selection.selected.candidate.parameter_map["p"] == 2 for fold in result.folds)
    assert {role for _, role in calls} == {
        SegmentRole.TRAIN,
        SegmentRole.VALIDATION,
        SegmentRole.TEST,
    }


@pytest.mark.parametrize(
    "state",
    [RunState.INCONCLUSIVE, RunState.REVIEW_REQUIRED, RunState.VALIDATION_FAILED],
)
def test_noncompleted_test_fold_makes_parent_inconclusive_without_stitch(state):
    plan = _plan()
    driver, _ = _driver(test_state=state)

    result = driver.run(
        plan, generate_grid((ParameterAxis("p", (1, 2)),))
    )

    assert result.state is RunState.INCONCLUSIVE
    assert result.stitched_oos is None
    assert result.n_folds_completed < result.n_folds_expected


def test_execution_failed_fold_is_not_partially_stitched():
    plan = _plan()
    driver, _ = _driver(fail_role=SegmentRole.TEST)
    result = driver.run(
        plan, generate_grid((ParameterAxis("p", (1, 2)),))
    )
    assert result.state is RunState.INCONCLUSIVE
    assert result.stitched_oos is None
    assert result.folds[-1].state is RunState.EXECUTION_FAILED


def test_nested_walk_forward_is_deterministic():
    plan = _plan()
    candidates = generate_grid((ParameterAxis("p", (2, 1)),))
    first, _ = _driver()
    second, _ = _driver()

    a = first.run(plan, candidates)
    b = second.run(plan, tuple(reversed(candidates)))

    assert [
        fold.selection.selected.candidate.candidate_id for fold in a.folds
    ] == [
        fold.selection.selected.candidate.candidate_id for fold in b.folds
    ]
    assert a.stitched_oos.expectancy_r == b.stitched_oos.expectancy_r
    assert a.stitched_oos.fold_net_pnl == b.stitched_oos.fold_net_pnl
