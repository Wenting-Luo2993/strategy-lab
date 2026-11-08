from __future__ import annotations

from types import SimpleNamespace

import pytest

from vibe.research_pipeline.optimization import CandidateSpec
from vibe.research_pipeline.robustness import (
    RobustnessOrchestrator,
    adjacent_candidates,
    seeded_bootstrap_expectancies,
)


def _candidate(name: str, coordinates: tuple[int, ...]) -> CandidateSpec:
    return CandidateSpec(name, (("x", coordinates[0]),), coordinates)


def test_neighbors_are_manhattan_adjacent_and_deterministically_sorted():
    selected = _candidate("selected", (1, 1))
    candidates = (
        _candidate("z", (2, 1)),
        _candidate("diagonal", (2, 2)),
        selected,
        _candidate("a", (1, 0)),
    )

    assert tuple(item.candidate_id for item in adjacent_candidates(
        selected, candidates
    )) == ("a", "z")


def test_seeded_bootstrap_is_reproducible_and_validates_inputs():
    first = seeded_bootstrap_expectancies((1.0, -1.0, 0.5), seed=7, replicates=8)
    second = seeded_bootstrap_expectancies((1.0, -1.0, 0.5), seed=7, replicates=8)

    assert first == second
    assert len(first) == 8
    with pytest.raises(ValueError, match="non-negative"):
        seeded_bootstrap_expectancies((1.0,), seed=-1)
    with pytest.raises(ValueError, match="positive"):
        seeded_bootstrap_expectancies((1.0,), seed=1, replicates=0)


def test_orchestration_records_cost_cliff_regime_and_seed_provenance():
    selected = _candidate("selected", (1,))
    neighbor = _candidate("neighbor", (2,))
    evidence = RobustnessOrchestrator(
        seed=11, bootstrap_replicates=5, cliff_threshold_r=0.20
    ).build(
        baseline_expectancy_r=0.30,
        baseline_r_multiples=(1.0, -0.5, 0.4),
        cost_scenarios=({"commission": 1.0}, {"commission": 2.0}),
        cost_runner=lambda row: 0.25 - row["commission"] * 0.1,
        selected_candidate=selected,
        candidates=(selected, neighbor),
        neighborhood_runner=lambda _candidate: -0.01,
        fold_expectancies_r=(0.2, -0.1, 0.3),
        validation_expectancies_r=(0.3, 0.1, 0.4),
        regime_returns={"high_vol": 30.0, "low_vol": 10.0},
        regimes_predeclared=True,
        n_candidates_evaluated=9,
    )

    assert evidence.cost_stress_expectancy_r == pytest.approx(0.05)
    assert evidence.sign_change_detected is True
    assert evidence.cliff_detected is True
    assert evidence.positive_fold_fraction == pytest.approx(2 / 3)
    assert evidence.validation_to_test_degradation_r == pytest.approx(2 / 15)
    assert evidence.max_single_regime_return_concentration == pytest.approx(0.75)
    assert evidence.subsample_seed == 11
    assert len(evidence.bootstrap_expectancies_r) == 5
    assert evidence.multiple_testing_diagnostic is None


def test_nested_orchestration_uses_frozen_fold_tests_for_stresses():
    selected = _candidate("selected", (1,))
    neighbor = _candidate("neighbor", (2,))
    fold = SimpleNamespace(
        fold_index=0,
        selection=SimpleNamespace(
            selected=SimpleNamespace(candidate=selected, score=0.3)
        ),
        test_result=SimpleNamespace(
            segment=object(),
            result=SimpleNamespace(
                overall=SimpleNamespace(
                    expectancy_r=0.2,
                    r_multiples=[0.4, 0.0],
                )
            ),
        ),
    )

    evidence = RobustnessOrchestrator(
        seed=7, bootstrap_replicates=4, cliff_threshold_r=0.25
    ).build_nested_walk_forward(
        SimpleNamespace(folds=(fold,)),
        (selected, neighbor),
        cost_scenarios=({"stop_ticks": 4.0},),
        cost_runner=lambda _fold, _candidate, _scenario: SimpleNamespace(
            result=SimpleNamespace(
                overall=SimpleNamespace(r_multiples=[0.1, 0.0])
            )
        ),
        neighborhood_runner=lambda _fold, _candidate: SimpleNamespace(
            result=SimpleNamespace(
                overall=SimpleNamespace(expectancy_r=-0.1)
            )
        ),
    )

    assert evidence.cost_stress_expectancy_r == pytest.approx(0.05)
    assert evidence.neighbor_expectancies_r == (-0.1,)
    assert evidence.neighbor_candidate_ids == ("fold-0:neighbor",)
    assert evidence.sign_change_detected is True
    assert evidence.cliff_detected is True
    assert evidence.subsample_seed == 7
    assert len(evidence.bootstrap_expectancies_r) == 4
    assert evidence.n_candidates_evaluated == 2
