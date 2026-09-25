from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from vibe.research_pipeline.contracts import (
    SegmentRole,
    SessionSegment,
    Severity,
    ValidationCategory,
    ValidationFinding,
)
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.optimization import (
    Optimizer,
    ParameterAxis,
    RulesetSegmentExecutor,
    SelectionIncompleteError,
    Selector,
    generate_grid,
)
from vibe.research_pipeline.validation import ValidationReport, ValidationScope
from vibe.common.ruleset.loader import RuleSetLoader


def _segment(role: SegmentRole) -> SessionSegment:
    starts = {
        SegmentRole.TRAIN: date(2020, 1, 2),
        SegmentRole.VALIDATION: date(2020, 2, 3),
    }
    start = starts[role]
    return SessionSegment(
        role=role, start_session=start, end_session=start, session_count=1
    )


def _report(state: RunState, scope: ValidationScope) -> ValidationReport:
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


def _executor(scores, calls, failures=frozenset()):
    def execute(candidate, segment):
        calls.append((candidate.candidate_id, segment.role))
        if (candidate.candidate_id, segment.role) in failures:
            raise RuntimeError("deliberate candidate failure")
        score = scores[(candidate.candidate_id, segment.role)]
        return SimpleNamespace(
            segment=segment,
            result=SimpleNamespace(score=score, trades=[]),
        )

    return execute


def _validator(states=None):
    states = states or {}

    def validate(candidate, result, scope):
        return _report(states.get((candidate.candidate_id, scope), RunState.COMPLETED), scope)

    return validate


def _scores(candidates, train=1.0, validation=None):
    validation = validation or {}
    return {
        **{
            (candidate.candidate_id, SegmentRole.TRAIN): train
            for candidate in candidates
        },
        **{
            (candidate.candidate_id, SegmentRole.VALIDATION): validation.get(
                candidate.coordinates, train
            )
            for candidate in candidates
        },
    }


def test_grid_is_deterministic_under_axis_and_value_reordering():
    first = generate_grid(
        (
            ParameterAxis("b", (20, 10)),
            ParameterAxis("a", (2, 1)),
        )
    )
    second = generate_grid(
        (
            ParameterAxis("a", (1, 2)),
            ParameterAxis("b", (10, 20)),
        )
    )
    assert first == second


def test_ruleset_executor_materializes_candidate_with_fresh_engine(monkeypatch):
    base = RuleSetLoader.from_name("orb_production").model_dump(mode="python")
    engines = []
    observed = []

    def engine_factory(ruleset):
        engine = SimpleNamespace(ruleset=ruleset)
        engines.append(engine)
        return engine

    def fake_run_segment(engine, symbol, segment, warmup_sessions):
        observed.append(
            (
                engine.ruleset.strategy.orb_duration_minutes,
                symbol,
                warmup_sessions,
            )
        )
        return SimpleNamespace(segment=segment, result=SimpleNamespace())

    monkeypatch.setattr(
        "vibe.research_pipeline.optimization.run_segment", fake_run_segment
    )
    executor = RulesetSegmentExecutor(
        base,
        {"duration": "strategy.orb_duration_minutes"},
        warmup_sessions=20,
        engine_factory=engine_factory,
    )
    candidate = generate_grid((ParameterAxis("duration", (15,)),))[0]
    segment = _segment(SegmentRole.TRAIN)

    executor(candidate, segment)
    executor(candidate, segment)

    assert len(engines) == 2
    assert engines[0] is not engines[1]
    assert observed == [(15, "QQQ", 20), (15, "QQQ", 20)]
    assert base["strategy"]["orb_duration_minutes"] != 15


def test_optimizer_uses_train_scope_and_reconciles_candidate_counts():
    candidates = generate_grid((ParameterAxis("p", (1, 2, 3)),))
    calls = []
    scores = _scores(candidates)
    optimizer = Optimizer(_executor(scores, calls), _validator(), lambda result: result.score)

    batch = optimizer.fit(_segment(SegmentRole.TRAIN), candidates)

    assert batch.complete
    assert batch.n_candidates_expected == 3
    assert batch.n_candidates_evaluated == 3
    assert batch.n_candidates_eligible == 3
    assert {role for _, role in calls} == {SegmentRole.TRAIN}
    assert {
        evaluation.validation_report.scope for evaluation in batch.evaluations
    } == {ValidationScope.SWEEP_ROW}


def test_optimizer_preserves_grid_hole_as_explicit_failure():
    candidates = generate_grid((ParameterAxis("p", (1, 2)),))
    calls = []
    failed = frozenset({(candidates[0].candidate_id, SegmentRole.TRAIN)})
    optimizer = Optimizer(
        _executor(_scores(candidates), calls, failed),
        _validator(),
        lambda result: result.score,
    )

    batch = optimizer.fit(_segment(SegmentRole.TRAIN), candidates)

    assert not batch.complete
    assert batch.n_candidates_expected == 2
    assert batch.n_candidates_evaluated == 1
    assert batch.failures[0].candidate_id == candidates[0].candidate_id


def test_selector_chooses_connected_plateau_medoid_not_argmax():
    candidates = generate_grid(
        (ParameterAxis("x", (0, 1, 2)), ParameterAxis("y", (0, 1, 2)))
    )
    validation_scores = {
        (0, 0): 1.00,
        (0, 1): 0.99,
        (0, 2): 0.98,
        (1, 0): 0.99,
        (1, 1): 0.97,
        (1, 2): 0.40,
        (2, 0): 0.20,
        (2, 1): 0.20,
        (2, 2): 0.20,
    }
    calls = []
    executor = _executor(_scores(candidates, validation=validation_scores), calls)
    optimizer = Optimizer(executor, _validator(), lambda result: result.score)
    batch = optimizer.fit(_segment(SegmentRole.TRAIN), candidates)
    selector = Selector(
        executor, _validator(), lambda result: result.score, plateau_tolerance=0.10
    )

    selected = selector.select(batch, _segment(SegmentRole.VALIDATION))

    # The peak is (0, 0), but the connected five-point plateau's medoid is (0, 1).
    assert selected.selected.candidate.coordinates == (0, 1)
    assert len(selected.neighborhood_candidate_ids) == 5
    assert selected.selection_margin == pytest.approx(0.99 - 0.40)
    assert {
        evaluation.validation_report.scope for evaluation in selected.evaluations
    } == {ValidationScope.CANDIDATE}


def test_selection_margin_preserves_negative_robustness_tradeoff():
    candidates = generate_grid((ParameterAxis("x", (0, 1, 2, 3, 4)),))
    # Tolerance includes the connected (0, 1, 2) plateau, whose medoid is x=1.
    # x=4 is disconnected and has a higher score than the chosen medoid.
    scores = _scores(
        candidates,
        validation={
            (0,): 1.0,
            (1,): 0.95,
            (2,): 0.94,
            (3,): 0.1,
            (4,): 0.97,
        },
    )
    calls = []
    executor = _executor(scores, calls)
    batch = Optimizer(executor, _validator(), lambda result: result.score).fit(
        _segment(SegmentRole.TRAIN), candidates
    )
    selection = Selector(
        executor, _validator(), lambda result: result.score, plateau_tolerance=0.10
    ).select(batch, _segment(SegmentRole.VALIDATION))

    assert selection.selected.candidate.coordinates == (1,)
    assert selection.selection_margin == pytest.approx(-0.02)


def test_selector_refuses_incomplete_training_grid():
    candidates = generate_grid((ParameterAxis("p", (1, 2)),))
    calls = []
    failed = frozenset({(candidates[0].candidate_id, SegmentRole.TRAIN)})
    executor = _executor(_scores(candidates), calls, failed)
    batch = Optimizer(executor, _validator(), lambda result: result.score).fit(
        _segment(SegmentRole.TRAIN), candidates
    )
    with pytest.raises(SelectionIncompleteError, match="incomplete"):
        Selector(executor, _validator(), lambda result: result.score).select(
            batch, _segment(SegmentRole.VALIDATION)
        )


@pytest.mark.parametrize(
    "state",
    [RunState.INCONCLUSIVE, RunState.REVIEW_REQUIRED, RunState.VALIDATION_FAILED],
)
def test_selector_refuses_noncompleted_validation_candidate(state):
    candidates = generate_grid((ParameterAxis("p", (1, 2)),))
    calls = []
    executor = _executor(_scores(candidates), calls)
    batch = Optimizer(executor, _validator(), lambda result: result.score).fit(
        _segment(SegmentRole.TRAIN), candidates
    )
    states = {(candidates[0].candidate_id, ValidationScope.CANDIDATE): state}
    with pytest.raises(SelectionIncompleteError, match="non-selectable") as exc:
        Selector(executor, _validator(states), lambda result: result.score).select(
            batch, _segment(SegmentRole.VALIDATION)
        )
    assert exc.value.n_candidates_expected == 2
    assert exc.value.n_candidates_evaluated == 2
    assert exc.value.n_candidates_eligible == 1
