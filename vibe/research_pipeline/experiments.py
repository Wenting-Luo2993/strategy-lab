"""Explicit experiment/fold provenance for nested walk-forward executions."""

from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from vibe.research_pipeline.evaluation import SegmentValidator


def _metrics(result: Any) -> dict[str, Any]:
    values = {
        "expectancy_r": result.overall.expectancy_r,
        "net_pnl": result.overall.total_pnl,
        "n_trades": result.overall.n_trades,
        "sharpe_ratio": result.equity.sharpe_ratio,
    }
    invalid = [key for key, value in values.items() if not math.isfinite(value)]
    return {
        **{key: value if key not in invalid else None for key, value in values.items()},
        "non_finite_metrics": invalid,
    }


def _evaluation(evaluation: Any) -> dict[str, Any]:
    return {
        "candidate_id": evaluation.candidate.candidate_id,
        "parameters": dict(evaluation.candidate.parameter_map),
        "score": evaluation.score if math.isfinite(evaluation.score) else None,
        "state": evaluation.validation_report.target_state.value,
        "findings": [
            finding.model_dump(mode="json")
            for finding in evaluation.validation_report.findings
        ],
        **_metrics(evaluation.segment_result.result),
    }


class ExperimentRecorder:
    """Keep study provenance out of the pure optimization driver.

    Each invocation gets its own identity; deduplicated segment runs can belong
    to multiple experiments without overwriting either experiment's history.
    """

    def __init__(
        self, store: Any, *, title: str, plan: Any, candidates: Any,
        axes: list[dict[str, Any]], symbol: str, strategy_id: str,
        methodology_version: str, record_test_only: bool = False,
        reference_configuration: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.plan = plan
        self._folds = {
            SegmentValidator.segment_key(segment): index
            for index, fold in enumerate(plan.folds)
            for segment in fold.segments
            if segment.role.value in {"train", "validation", "test"}
        }
        self.experiment_id = store.start_experiment(title, {
            "schema_version": 1,
            "symbol": symbol,
            "strategy_id": strategy_id,
            "methodology_version": methodology_version,
            "plan_hash": plan.plan_hash,
            "plan": plan.model_dump(mode="json"),
            "axes": axes,
            "candidates": [
                {"candidate_id": candidate.candidate_id,
                 "parameters": dict(candidate.parameter_map)}
                for candidate in candidates
            ],
            "objective": "net expectancy_r (mean net R per trade)",
            "training_policy": "Evaluate and validate every candidate; no performance-based training shortlist.",
            "selection_policy": "Validation plateau medoid (selector v1, tolerance 0.10). Freeze before test.",
            "record_test_only": record_test_only,
            "reference_configuration": reference_configuration,
        })
        self.recording_errors: list[str] = []

    def record_run(self, candidate: Any, segment: Any, outcome: Any) -> None:
        try:
            self.store.link_experiment_run(
                self.experiment_id,
                self._folds[SegmentValidator.segment_key(segment)],
                candidate.candidate_id, segment.role, outcome.run_id,
            )
        except Exception as exc:
            self.recording_errors.append(f"{type(exc).__name__}: {exc}")
            raise

    def finish(self, result: Any, *, recording_errors: list[str]) -> None:
        folds = []
        for fold in result.folds:
            selected = fold.selection
            folds.append({
                "fold_index": fold.fold_index,
                "state": fold.state.value,
                "reason": fold.reason,
                "train": [
                    _evaluation(evaluation)
                    for evaluation in (
                        fold.optimization.evaluations if fold.optimization else ()
                    )
                ],
                "train_failures": [
                    asdict(failure)
                    for failure in (
                        fold.optimization.failures if fold.optimization else ()
                    )
                ],
                "validation": [
                    _evaluation(evaluation)
                    for evaluation in fold.validation_evaluations
                ],
                "validation_failures": [
                    asdict(failure) for failure in fold.validation_failures
                ],
                "selection": {
                    "candidate_id": selected.selected.candidate.candidate_id,
                    "parameters": dict(selected.selected.candidate.parameter_map),
                    "neighborhood_candidate_ids": list(selected.neighborhood_candidate_ids),
                    "winner_score": selected.winner_score,
                    "runner_up_score": selected.runner_up_score,
                    "selection_margin": selected.selection_margin,
                    "selector_version": selected.selector_version,
                } if selected else None,
                "test": {
                    "candidate_id": selected.selected.candidate.candidate_id,
                    "parameters": dict(selected.selected.candidate.parameter_map),
                    "state": fold.test_validation.target_state.value,
                    "findings": [
                        finding.model_dump(mode="json")
                        for finding in fold.test_validation.findings
                    ],
                    **_metrics(fold.test_result.result),
                } if fold.test_result is not None and fold.test_validation is not None else None,
            })
        self.store.finish_experiment(self.experiment_id, result.state.value, {
            "folds": folds,
            "n_folds_expected": result.n_folds_expected,
            "n_folds_completed": result.n_folds_completed,
            "reason": result.reason,
            "recording_errors": [*recording_errors, *self.recording_errors],
            "stitched_oos": {
                "n_trades": result.stitched_oos.n_trades,
                "expectancy_r": result.stitched_oos.expectancy_r,
                "fold_net_pnl": list(result.stitched_oos.fold_net_pnl),
            } if result.stitched_oos else None,
        })
