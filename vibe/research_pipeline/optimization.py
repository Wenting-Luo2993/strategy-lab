"""Pure optimization and validation-selection seams for nested research.

The optimizer never receives validation or test data.  The selector never
receives test data.  Both return immutable evidence and leave persistence and
lifecycle transitions to their caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from itertools import product
from pathlib import Path
from statistics import fmean
from typing import Any, Callable, Mapping, Protocol, Sequence

import yaml

from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.data.paths import resolve_market_data_dir
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.research_pipeline.contracts import SessionSegment
from vibe.research_pipeline.hashing import canonical_json, hash_object
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.segment_runner import SegmentResult, run_segment
from vibe.research_pipeline.validation import ValidationReport, ValidationScope

__all__ = [
    "CandidateExecutor",
    "CandidateFailure",
    "CandidateSpec",
    "CandidateEvaluation",
    "OptimizationBatch",
    "OptimizationIncompleteError",
    "Optimizer",
    "ParameterAxis",
    "RulesetSegmentExecutor",
    "SelectionIncompleteError",
    "SelectionResult",
    "Selector",
    "ValidationEvaluation",
    "generate_grid",
]


class CandidateExecutor(Protocol):
    def __call__(
        self, candidate: "CandidateSpec", segment: SessionSegment
    ) -> SegmentResult: ...


CandidateValidator = Callable[
    ["CandidateSpec", SegmentResult, ValidationScope], ValidationReport
]
CandidateScorer = Callable[[BacktestResult], float]


class RulesetSegmentExecutor:
    """Materialize one candidate into a fresh engine and run one segment.

    A new engine is constructed for every invocation, so portfolio, strategy,
    indicator, and cache state cannot cross candidates, roles, or folds.
    Validation remains injected separately because P6 requires evidence beyond
    the engine result (data integrity, leakage checks, and registry hashes).
    """

    def __init__(
        self,
        base_ruleset: Mapping[str, Any],
        parameter_paths: Mapping[str, str],
        *,
        data_dir: Path | str | None = None,
        warmup_sessions: int = 0,
        engine_kwargs: Mapping[str, Any] | None = None,
        engine_factory: Callable[[StrategyRuleSet], BacktestEngine] | None = None,
    ) -> None:
        if warmup_sessions < 0:
            raise ValueError("warmup_sessions must be non-negative")
        self._base_ruleset = deepcopy(dict(base_ruleset))
        self._parameter_paths = dict(parameter_paths)
        self._warmup_sessions = warmup_sessions
        self._engine_kwargs = dict(engine_kwargs or {})
        if engine_factory is not None:
            self._engine_factory = engine_factory
        else:
            resolved_data_dir = resolve_market_data_dir(data_dir)
            self._engine_factory = lambda ruleset: BacktestEngine(
                ruleset=ruleset,
                data_dir=resolved_data_dir,
                **self._engine_kwargs,
            )

    @classmethod
    def from_yaml(
        cls,
        ruleset_path: Path | str,
        parameter_paths: Mapping[str, str],
        **kwargs: Any,
    ) -> "RulesetSegmentExecutor":
        with Path(ruleset_path).open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        if not isinstance(config, dict):
            raise ValueError(f"Ruleset {ruleset_path} must contain a mapping")
        return cls(config, parameter_paths, **kwargs)

    def __call__(
        self, candidate: "CandidateSpec", segment: SessionSegment
    ) -> SegmentResult:
        parameters = candidate.parameter_map
        missing = sorted(set(self._parameter_paths) - set(parameters))
        extra = sorted(set(parameters) - set(self._parameter_paths))
        if missing or extra:
            raise ValueError(
                f"Candidate parameters do not match declared paths; "
                f"missing={missing}, extra={extra}"
            )

        config = deepcopy(self._base_ruleset)
        for name, path in sorted(self._parameter_paths.items()):
            self._set_nested(config, path, parameters[name])
        ruleset = StrategyRuleSet(**config)
        engine = self._engine_factory(ruleset)
        symbol = ruleset.instruments.symbols[0]
        return run_segment(
            engine,
            symbol,
            segment,
            warmup_sessions=self._warmup_sessions,
        )

    @staticmethod
    def _set_nested(config: dict[str, Any], path: str, value: Any) -> None:
        keys = path.split(".")
        if not all(keys):
            raise ValueError(f"Invalid empty parameter path {path!r}")
        current = config
        for key in keys[:-1]:
            child = current.get(key)
            if not isinstance(child, dict):
                raise ValueError(
                    f"Parameter path {path!r} does not exist in the base ruleset"
                )
            current = child
        if keys[-1] not in current:
            raise ValueError(
                f"Parameter path {path!r} does not exist in the base ruleset"
            )
        current[keys[-1]] = value


def _value_key(value: Any) -> tuple[str, Any]:
    if isinstance(value, bool):
        return ("bool", int(value))
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    return (type(value).__name__, canonical_json(value))


@dataclass(frozen=True)
class ParameterAxis:
    name: str
    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Parameter axis name must be non-empty")
        if not self.values:
            raise ValueError(f"Parameter axis {self.name!r} has no values")
        if len({_value_key(value) for value in self.values}) != len(self.values):
            raise ValueError(f"Parameter axis {self.name!r} contains duplicate values")


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    parameters: tuple[tuple[str, Any], ...]
    coordinates: tuple[int, ...]

    @classmethod
    def create(
        cls,
        parameters: Mapping[str, Any],
        coordinates: Sequence[int],
    ) -> "CandidateSpec":
        ordered = tuple(sorted(parameters.items()))
        return cls(
            candidate_id=f"candidate-{hash_object(dict(ordered))[:16]}",
            parameters=ordered,
            coordinates=tuple(coordinates),
        )

    @property
    def parameter_map(self) -> dict[str, Any]:
        return dict(self.parameters)


def generate_grid(axes: Sequence[ParameterAxis]) -> tuple[CandidateSpec, ...]:
    """Generate a canonical Cartesian grid independent of input ordering."""
    if not axes:
        raise ValueError("At least one parameter axis is required")
    normalized = tuple(
        ParameterAxis(
            axis.name,
            tuple(sorted(axis.values, key=_value_key)),
        )
        for axis in sorted(axes, key=lambda axis: axis.name)
    )
    if len({axis.name for axis in normalized}) != len(normalized):
        raise ValueError("Parameter axis names must be unique")

    candidates = []
    coordinate_ranges = [range(len(axis.values)) for axis in normalized]
    for coordinates in product(*coordinate_ranges):
        parameters = {
            axis.name: axis.values[index]
            for axis, index in zip(normalized, coordinates)
        }
        candidates.append(CandidateSpec.create(parameters, coordinates))
    return tuple(sorted(candidates, key=lambda candidate: candidate.candidate_id))


@dataclass(frozen=True)
class CandidateFailure:
    candidate_id: str
    phase: str
    message: str


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: CandidateSpec
    segment: SessionSegment
    segment_result: SegmentResult
    validation_report: ValidationReport
    score: float


@dataclass(frozen=True)
class OptimizationBatch:
    segment: SessionSegment
    evaluations: tuple[CandidateEvaluation, ...]
    failures: tuple[CandidateFailure, ...]
    n_candidates_expected: int
    n_candidates_evaluated: int
    n_candidates_eligible: int

    @property
    def complete(self) -> bool:
        return (
            not self.failures
            and self.n_candidates_evaluated == self.n_candidates_expected
        )

    @property
    def eligible(self) -> tuple[CandidateEvaluation, ...]:
        return tuple(
            evaluation
            for evaluation in self.evaluations
            if evaluation.validation_report.target_state is RunState.COMPLETED
        )


class OptimizationIncompleteError(RuntimeError):
    pass


class Optimizer:
    """Evaluate a complete candidate grid on one TRAIN segment."""

    def __init__(
        self,
        executor: CandidateExecutor,
        validator: CandidateValidator,
        scorer: CandidateScorer,
    ) -> None:
        self._executor = executor
        self._validator = validator
        self._scorer = scorer

    def fit(
        self,
        segment: SessionSegment,
        candidates: Sequence[CandidateSpec],
    ) -> OptimizationBatch:
        evaluations: list[CandidateEvaluation] = []
        failures: list[CandidateFailure] = []

        for candidate in sorted(candidates, key=lambda item: item.candidate_id):
            try:
                segment_result = self._executor(candidate, segment)
                report = self._validator(
                    candidate, segment_result, ValidationScope.SWEEP_ROW
                )
                evaluations.append(
                    CandidateEvaluation(
                        candidate=candidate,
                        segment=segment,
                        segment_result=segment_result,
                        validation_report=report,
                        score=float(self._scorer(segment_result.result)),
                    )
                )
            except Exception as exc:
                failures.append(
                    CandidateFailure(
                        candidate_id=candidate.candidate_id,
                        phase="train",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )

        eligible = sum(
            evaluation.validation_report.target_state is RunState.COMPLETED
            for evaluation in evaluations
        )
        return OptimizationBatch(
            segment=segment,
            evaluations=tuple(evaluations),
            failures=tuple(failures),
            n_candidates_expected=len(candidates),
            n_candidates_evaluated=len(evaluations),
            n_candidates_eligible=eligible,
        )


@dataclass(frozen=True)
class ValidationEvaluation:
    candidate: CandidateSpec
    segment: SessionSegment
    segment_result: SegmentResult
    validation_report: ValidationReport
    score: float


@dataclass(frozen=True)
class SelectionResult:
    selected: ValidationEvaluation
    evaluations: tuple[ValidationEvaluation, ...]
    neighborhood_candidate_ids: tuple[str, ...]
    winner_score: float
    runner_up_score: float | None
    selection_margin: float | None
    n_candidates_evaluated: int
    n_candidates_eligible: int
    selector_version: int = 1


class SelectionIncompleteError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        evaluations: Sequence[ValidationEvaluation] = (),
        failures: Sequence[CandidateFailure] = (),
        n_candidates_expected: int = 0,
    ) -> None:
        super().__init__(message)
        self.evaluations = tuple(evaluations)
        self.failures = tuple(failures)
        self.n_candidates_expected = n_candidates_expected

    @property
    def n_candidates_evaluated(self) -> int:
        return len(self.evaluations)

    @property
    def n_candidates_eligible(self) -> int:
        return sum(
            evaluation.validation_report.target_state is RunState.COMPLETED
            for evaluation in self.evaluations
        )


class Selector:
    """Select the deterministic medoid of a connected validation plateau."""

    def __init__(
        self,
        executor: CandidateExecutor,
        validator: CandidateValidator,
        scorer: CandidateScorer,
        *,
        plateau_tolerance: float = 0.10,
    ) -> None:
        if not 0.0 <= plateau_tolerance <= 1.0:
            raise ValueError("plateau_tolerance must be between 0 and 1")
        self._executor = executor
        self._validator = validator
        self._scorer = scorer
        self._plateau_tolerance = plateau_tolerance

    def select(
        self,
        optimization: OptimizationBatch,
        validation_segment: SessionSegment,
    ) -> SelectionResult:
        if not optimization.complete:
            raise SelectionIncompleteError(
                "Training candidate grid is incomplete; selecting from survivors "
                "would launder candidate failures.",
                n_candidates_expected=optimization.n_candidates_eligible,
            )
        if not optimization.eligible:
            raise SelectionIncompleteError(
                "No P6-completed TRAIN candidate is eligible",
                n_candidates_expected=0,
            )

        evaluations: list[ValidationEvaluation] = []
        failures: list[CandidateFailure] = []
        for trained in optimization.eligible:
            candidate = trained.candidate
            try:
                segment_result = self._executor(candidate, validation_segment)
                report = self._validator(
                    candidate, segment_result, ValidationScope.CANDIDATE
                )
                evaluations.append(
                    ValidationEvaluation(
                        candidate=candidate,
                        segment=validation_segment,
                        segment_result=segment_result,
                        validation_report=report,
                        score=float(self._scorer(segment_result.result)),
                    )
                )
            except Exception as exc:
                failures.append(
                    CandidateFailure(
                        candidate_id=candidate.candidate_id,
                        phase="validation",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )

        if failures or len(evaluations) != optimization.n_candidates_eligible:
            raise SelectionIncompleteError(
                "Validation candidate grid is incomplete; selecting from survivors "
                "is forbidden.",
                evaluations=evaluations,
                failures=failures,
                n_candidates_expected=optimization.n_candidates_eligible,
            )
        eligible = tuple(
            evaluation
            for evaluation in evaluations
            if evaluation.validation_report.target_state is RunState.COMPLETED
        )
        if len(eligible) != len(evaluations):
            states = sorted(
                {
                    evaluation.validation_report.target_state.value
                    for evaluation in evaluations
                    if evaluation.validation_report.target_state
                    is not RunState.COMPLETED
                }
            )
            raise SelectionIncompleteError(
                "Validation produced non-selectable candidate outcomes "
                f"{states}; the fold is inconclusive.",
                evaluations=evaluations,
                n_candidates_expected=optimization.n_candidates_eligible,
            )
        if not eligible:
            raise SelectionIncompleteError(
                "No validation candidate is eligible",
                evaluations=evaluations,
                n_candidates_expected=optimization.n_candidates_eligible,
            )

        scores = [evaluation.score for evaluation in eligible]
        best = max(scores)
        worst = min(scores)
        threshold = best - self._plateau_tolerance * (best - worst)
        top = tuple(evaluation for evaluation in eligible if evaluation.score >= threshold)
        components = self._connected_components(top)
        chosen = min(
            components,
            key=lambda component: (
                -len(component),
                -fmean(item.score for item in component),
                -min(item.score for item in component),
                min(item.candidate.candidate_id for item in component),
            ),
        )
        selected = min(
            chosen,
            key=lambda item: (
                sum(
                    self._manhattan(
                        item.candidate.coordinates, other.candidate.coordinates
                    )
                    for other in chosen
                ),
                -item.score,
                item.candidate.candidate_id,
            ),
        )
        chosen_ids = frozenset(item.candidate.candidate_id for item in chosen)
        outside = [
            item.score
            for item in eligible
            if item.candidate.candidate_id not in chosen_ids
        ]
        runner_up = max(outside) if outside else None
        margin = selected.score - runner_up if runner_up is not None else None

        return SelectionResult(
            selected=selected,
            evaluations=tuple(
                sorted(eligible, key=lambda item: item.candidate.candidate_id)
            ),
            neighborhood_candidate_ids=tuple(sorted(chosen_ids)),
            winner_score=selected.score,
            runner_up_score=runner_up,
            selection_margin=margin,
            n_candidates_evaluated=len(evaluations),
            n_candidates_eligible=len(eligible),
        )

    @staticmethod
    def _manhattan(left: tuple[int, ...], right: tuple[int, ...]) -> int:
        return sum(abs(a - b) for a, b in zip(left, right))

    @classmethod
    def _adjacent(cls, left: CandidateSpec, right: CandidateSpec) -> bool:
        deltas = [
            abs(a - b) for a, b in zip(left.coordinates, right.coordinates)
        ]
        return sum(delta == 1 for delta in deltas) == 1 and all(
            delta in (0, 1) for delta in deltas
        )

    @classmethod
    def _connected_components(
        cls, evaluations: Sequence[ValidationEvaluation]
    ) -> tuple[tuple[ValidationEvaluation, ...], ...]:
        by_id = {
            evaluation.candidate.candidate_id: evaluation
            for evaluation in evaluations
        }
        unseen = set(by_id)
        components = []
        while unseen:
            root = min(unseen)
            unseen.remove(root)
            stack = [root]
            member_ids = []
            while stack:
                current = stack.pop()
                member_ids.append(current)
                neighbors = sorted(
                    candidate_id
                    for candidate_id in unseen
                    if cls._adjacent(
                        by_id[current].candidate, by_id[candidate_id].candidate
                    )
                )
                for candidate_id in neighbors:
                    unseen.remove(candidate_id)
                    stack.append(candidate_id)
            components.append(
                tuple(by_id[candidate_id] for candidate_id in sorted(member_ids))
            )
        return tuple(components)
