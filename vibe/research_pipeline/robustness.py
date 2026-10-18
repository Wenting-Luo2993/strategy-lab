"""Deterministic robustness evidence builders for research studies."""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from vibe.research_pipeline.hashing import hash_object
from vibe.research_pipeline.optimization import CandidateSpec
from vibe.research_pipeline.reliability import RobustnessEvidence


def adjacent_candidates(
    selected: CandidateSpec,
    candidates: Sequence[CandidateSpec],
) -> tuple[CandidateSpec, ...]:
    """Return deterministic Manhattan-distance-one parameter neighbors."""
    return tuple(sorted(
        (
            candidate for candidate in candidates
            if candidate.candidate_id != selected.candidate_id
            and len(candidate.coordinates) == len(selected.coordinates)
            and sum(
                abs(left - right)
                for left, right in zip(candidate.coordinates, selected.coordinates)
            ) == 1
        ),
        key=lambda candidate: candidate.candidate_id,
    ))


def seeded_bootstrap_expectancies(
    r_multiples: Sequence[float],
    *,
    seed: int,
    replicates: int = 200,
) -> tuple[float, ...]:
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if replicates <= 0:
        raise ValueError("replicates must be positive")
    if not r_multiples:
        return ()
    rng = random.Random(seed)
    sample = tuple(float(value) for value in r_multiples)
    return tuple(
        statistics.fmean(rng.choice(sample) for _ in sample)
        for _ in range(replicates)
    )


@dataclass(frozen=True)
class RobustnessOrchestrator:
    """Compose declared stress runs without inventing unavailable statistics."""

    seed: int = 17_101
    bootstrap_replicates: int = 200
    cliff_threshold_r: float = 0.25

    def build(
        self,
        *,
        baseline_expectancy_r: float,
        baseline_r_multiples: Sequence[float],
        cost_scenarios: Sequence[Mapping[str, float]],
        cost_runner: Callable[[Mapping[str, float]], float],
        selected_candidate: CandidateSpec,
        candidates: Sequence[CandidateSpec],
        neighborhood_runner: Callable[[CandidateSpec], float],
        fold_expectancies_r: Sequence[float],
        validation_expectancies_r: Sequence[float],
        regime_returns: Mapping[str, float],
        regimes_predeclared: bool,
        n_candidates_evaluated: int,
    ) -> RobustnessEvidence:
        cost_rows = tuple(
            {
                **dict(scenario),
                "expectancy_r": float(cost_runner(scenario)),
            }
            for scenario in cost_scenarios
        )
        conservative_cost = (
            min(float(row["expectancy_r"]) for row in cost_rows)
            if cost_rows else None
        )
        neighbors = adjacent_candidates(selected_candidate, candidates)
        neighbor_values = tuple(
            float(neighborhood_runner(candidate)) for candidate in neighbors
        )
        sign_change = (
            any(value < 0 <= baseline_expectancy_r for value in neighbor_values)
            or any(value >= 0 > baseline_expectancy_r for value in neighbor_values)
            if neighbor_values else None
        )
        cliff = (
            any(
                abs(value - baseline_expectancy_r) > self.cliff_threshold_r
                for value in neighbor_values
            )
            if neighbor_values else None
        )
        positive_fold_fraction = (
            sum(value > 0 for value in fold_expectancies_r)
            / len(fold_expectancies_r)
            if fold_expectancies_r else None
        )
        degradation = (
            statistics.fmean(
                validation - test
                for validation, test in zip(
                    validation_expectancies_r, fold_expectancies_r
                )
            )
            if fold_expectancies_r and validation_expectancies_r else None
        )
        total_abs = sum(abs(value) for value in regime_returns.values())
        concentration = (
            max(abs(value) for value in regime_returns.values()) / total_abs
            if regime_returns and total_abs > 0 else None
        )
        bootstraps = seeded_bootstrap_expectancies(
            baseline_r_multiples,
            seed=self.seed,
            replicates=self.bootstrap_replicates,
        )
        sample_identity = hash_object({
            "seed": self.seed,
            "replicates": self.bootstrap_replicates,
            "r_multiples": list(baseline_r_multiples),
        })
        return RobustnessEvidence(
            cost_stress_expectancy_r=conservative_cost,
            cost_stress_scenarios=cost_rows,
            neighbor_expectancies_r=neighbor_values,
            neighbor_candidate_ids=tuple(item.candidate_id for item in neighbors),
            sign_change_detected=sign_change,
            cliff_detected=cliff,
            positive_fold_fraction=positive_fold_fraction,
            worst_fold_expectancy_r=min(fold_expectancies_r)
            if fold_expectancies_r else None,
            validation_to_test_degradation_r=degradation,
            regime_count=len(regime_returns) if regime_returns else None,
            max_single_regime_return_concentration=concentration,
            regimes_predeclared=regimes_predeclared,
            subsample_seed=self.seed,
            subsample_identity=sample_identity,
            bootstrap_expectancies_r=bootstraps,
            n_candidates_evaluated=n_candidates_evaluated,
            multiple_testing_diagnostic=None,
        )
