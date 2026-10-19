"""Deterministic robustness evidence builders for research studies."""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

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

    def build_nested_walk_forward(
        self,
        study: Any,
        candidates: Sequence[CandidateSpec],
        *,
        cost_scenarios: Sequence[Mapping[str, float]],
        cost_runner: Callable[[Any, CandidateSpec, Mapping[str, float]], Any],
        neighborhood_runner: Callable[[Any, CandidateSpec], Any],
        regime_returns: Mapping[str, float] | None = None,
        regimes_predeclared: bool = False,
    ) -> RobustnessEvidence:
        """Build study-level evidence from predeclared post-selection stresses.

        Cost and neighborhood runs use each fold's already-frozen candidate and
        TEST segment. They are diagnostics only: no result is fed back into
        candidate selection.
        """
        folds = tuple(getattr(study, "folds", ()))
        baseline_r_multiples: list[float] = []
        fold_expectancies: list[float] = []
        validation_expectancies: list[float] = []
        neighbor_values: list[float] = []
        neighbor_ids: list[str] = []
        sign_change = False
        cliff = False

        for fold in folds:
            if fold.selection is None or fold.test_result is None:
                continue
            selected = fold.selection.selected.candidate
            baseline = float(fold.test_result.result.overall.expectancy_r)
            fold_expectancies.append(baseline)
            validation_expectancies.append(float(fold.selection.selected.score))
            baseline_r_multiples.extend(
                float(value)
                for value in fold.test_result.result.overall.r_multiples
            )
            for neighbor in adjacent_candidates(selected, candidates):
                stressed = neighborhood_runner(fold, neighbor)
                value = float(stressed.result.overall.expectancy_r)
                neighbor_values.append(value)
                neighbor_ids.append(f"fold-{fold.fold_index}:{neighbor.candidate_id}")
                sign_change = sign_change or (
                    value < 0 <= baseline or value >= 0 > baseline
                )
                cliff = cliff or abs(value - baseline) > self.cliff_threshold_r

        cost_rows: list[dict[str, float]] = []
        for scenario in cost_scenarios:
            stressed_r: list[float] = []
            for fold in folds:
                if fold.selection is None or fold.test_result is None:
                    continue
                stressed = cost_runner(
                    fold,
                    fold.selection.selected.candidate,
                    scenario,
                )
                stressed_r.extend(
                    float(value) for value in stressed.result.overall.r_multiples
                )
            cost_rows.append({
                **dict(scenario),
                "expectancy_r": (
                    statistics.fmean(stressed_r) if stressed_r else float("nan")
                ),
            })

        valid_cost_rows = tuple(
            row for row in cost_rows
            if isinstance(row["expectancy_r"], float)
            and row["expectancy_r"] == row["expectancy_r"]
        )
        conservative_cost = (
            min(row["expectancy_r"] for row in valid_cost_rows)
            if valid_cost_rows else None
        )
        returns = dict(regime_returns or {})
        total_abs = sum(abs(value) for value in returns.values())
        concentration = (
            max(abs(value) for value in returns.values()) / total_abs
            if returns and total_abs > 0 else None
        )
        bootstraps = seeded_bootstrap_expectancies(
            baseline_r_multiples,
            seed=self.seed,
            replicates=self.bootstrap_replicates,
        )
        return RobustnessEvidence(
            cost_stress_expectancy_r=conservative_cost,
            cost_stress_scenarios=valid_cost_rows,
            neighbor_expectancies_r=tuple(neighbor_values),
            neighbor_candidate_ids=tuple(neighbor_ids),
            sign_change_detected=sign_change if neighbor_values else None,
            cliff_detected=cliff if neighbor_values else None,
            positive_fold_fraction=(
                sum(value > 0 for value in fold_expectancies)
                / len(fold_expectancies)
                if fold_expectancies else None
            ),
            worst_fold_expectancy_r=(
                min(fold_expectancies) if fold_expectancies else None
            ),
            validation_to_test_degradation_r=(
                statistics.fmean(
                    validation - test
                    for validation, test in zip(
                        validation_expectancies, fold_expectancies
                    )
                )
                if fold_expectancies else None
            ),
            regime_count=len(returns) if returns else None,
            max_single_regime_return_concentration=concentration,
            regimes_predeclared=regimes_predeclared,
            subsample_seed=self.seed,
            subsample_identity=hash_object({
                "seed": self.seed,
                "replicates": self.bootstrap_replicates,
                "r_multiples": baseline_r_multiples,
            }),
            bootstrap_expectancies_r=bootstraps,
            n_candidates_evaluated=len(candidates) * len(folds),
            multiple_testing_diagnostic=None,
        )
