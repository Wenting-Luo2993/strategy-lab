"""Versioned, deterministic study-reliability policy and evaluator.

Reliability is deliberately a research decision, not an engine assertion.
Passing this policy means the predeclared evidence cleared the registered
decision boundary; it is not a claim of future profit or economic certainty.
"""

from __future__ import annotations

import math
import statistics
from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vibe.backtester.core.integrity import EngineIntegrityStatus
from vibe.research_pipeline.hashing import hash_object

RELIABILITY_POLICY_VERSION = 2


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReliabilityVerdict(str, Enum):
    RELIABLE = "reliable"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    REVIEW_REQUIRED = "review_required"


class CriterionOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    REVIEW = "review"
    ADVISORY = "advisory"


class ReliabilityPolicy(_Frozen):
    """Conservative policy with explicit, typed pre-freeze overrides."""

    version: int = Field(RELIABILITY_POLICY_VERSION, frozen=True)
    min_stitched_oos_trades: int = Field(100, ge=1)
    min_stitched_oos_sessions: int = Field(120, ge=1)
    min_stitched_expectancy_r: float = 0.0
    min_positive_fold_fraction: float = Field(0.60, ge=0.0, le=1.0)
    max_validation_to_test_degradation_r: float = Field(0.25, ge=0.0)
    max_parameter_manhattan_movement: float = Field(1.5, ge=0.0)
    min_plateau_width: int = Field(2, ge=1)
    min_selection_margin_r: float | None = Field(None, ge=0.0)
    require_cost_stress_survival: bool = True
    min_cost_stress_expectancy_r: float = 0.0
    require_parameter_neighborhood: bool = True
    min_profitable_neighbor_fraction: float = Field(0.50, ge=0.0, le=1.0)
    require_regime_evidence: bool = True
    min_regime_count: int = Field(2, ge=1)
    max_single_regime_return_concentration: float = Field(0.80, gt=0.0, le=1.0)
    require_seeded_subsampling: bool = True
    subsampling_is_advisory: bool = True
    require_shared_capital_for_multi_symbol: bool = True
    plausibility_findings_require_review: bool = True
    expectancy_confidence_level: float = Field(0.95, gt=0.0, lt=1.0)
    use_expectancy_confidence_interval: bool = True

    @model_validator(mode="after")
    def _supported_version(self) -> "ReliabilityPolicy":
        if self.version not in {1, RELIABILITY_POLICY_VERSION}:
            raise ValueError(
                f"Unsupported reliability policy version {self.version}; "
                f"this binary implements immutable v1 and v{RELIABILITY_POLICY_VERSION}."
            )
        if self.version == 1 and (
            self.expectancy_confidence_level != 0.95
            or self.use_expectancy_confidence_interval is not True
        ):
            raise ValueError("Policy v1 cannot override Policy v2 confidence fields")
        return self

    @property
    def policy_hash(self) -> str:
        payload = self.model_dump(mode="python")
        if self.version == 1:
            payload.pop("expectancy_confidence_level")
            payload.pop("use_expectancy_confidence_interval")
        return hash_object(payload)


class ReliabilityCriterion(_Frozen):
    criterion_id: str
    outcome: CriterionOutcome
    observed: Any = None
    expected: str
    evidence_references: tuple[str, ...] = ()


class StudyReliabilityReport(_Frozen):
    verdict: ReliabilityVerdict
    policy_version: int
    policy_hash: str
    engine_integrity_complete: bool
    procedure_state: str
    stitched_oos_summary: Mapping[str, Any]
    fold_stability_summary: Mapping[str, Any]
    robustness_summary: Mapping[str, Any]
    holdout_summary: Mapping[str, Any]
    statistical_confidence: Mapping[str, Any]
    verdict_reason: str
    criteria: tuple[ReliabilityCriterion, ...]


class RobustnessEvidence(_Frozen):
    """Reproducible robustness evidence, without unsupported invented stats."""

    version: int = 1
    cost_stress_expectancy_r: float | None = None
    cost_stress_scenarios: tuple[Mapping[str, Any], ...] = ()
    neighbor_expectancies_r: tuple[float, ...] = ()
    neighbor_candidate_ids: tuple[str, ...] = ()
    sign_change_detected: bool | None = None
    cliff_detected: bool | None = None
    positive_fold_fraction: float | None = None
    worst_fold_expectancy_r: float | None = None
    validation_to_test_degradation_r: float | None = None
    regime_count: int | None = None
    max_single_regime_return_concentration: float | None = None
    regimes_predeclared: bool | None = None
    regime_definitions: tuple[Mapping[str, Any], ...] = ()
    regime_observations: tuple[Mapping[str, Any], ...] = ()
    regime_unclassified_trades: int = 0
    subsample_seed: int | None = None
    subsample_identity: str | None = None
    bootstrap_expectancies_r: tuple[float, ...] = ()
    n_candidates_evaluated: int | None = None
    multiple_testing_diagnostic: Mapping[str, Any] | None = None

    @property
    def provenance_hash(self) -> str:
        return hash_object(self)


def _criterion(
    criterion_id: str,
    outcome: CriterionOutcome,
    observed: Any,
    expected: str,
    *references: str,
) -> ReliabilityCriterion:
    return ReliabilityCriterion(
        criterion_id=criterion_id,
        outcome=outcome,
        observed=observed,
        expected=expected,
        evidence_references=tuple(references),
    )


def _fold_expectancies(study: Any) -> list[float]:
    values: list[float] = []
    for fold in getattr(study, "folds", ()):
        test = getattr(fold, "test_result", None)
        result = getattr(test, "result", None)
        if result is not None:
            values.append(float(result.overall.expectancy_r))
    return values


def _validation_scores(study: Any) -> list[float]:
    return [
        float(fold.selection.selected.score)
        for fold in getattr(study, "folds", ())
        if getattr(fold, "selection", None) is not None
    ]


def _stitched_r_multiples(study: Any) -> list[float]:
    values: list[float] = []
    for fold in getattr(study, "folds", ()):
        result = getattr(getattr(fold, "test_result", None), "result", None)
        overall = getattr(result, "overall", None)
        for value in getattr(overall, "r_multiples", ()) if overall is not None else ():
            numeric = float(value)
            if math.isfinite(numeric):
                values.append(numeric)
    return values


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations = 200
    epsilon = 3e-14
    floor = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < floor:
        d = floor
    d = 1.0 / d
    result = d
    for iteration in range(1, maximum_iterations + 1):
        even = 2 * iteration
        numerator = iteration * (b - iteration) * x / (
            (qam + even) * (a + even)
        )
        d = 1.0 + numerator * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + numerator / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        result *= d * c

        numerator = -(a + iteration) * (qab + iteration) * x / (
            (a + even) * (qap + even)
        )
        d = 1.0 + numerator * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + numerator / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) <= epsilon:
            return result
    raise ArithmeticError("Incomplete beta continued fraction did not converge")


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def _student_t_cdf(value: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("degrees_of_freedom must be positive")
    if value == 0.0:
        return 0.5
    ratio = degrees_of_freedom / (degrees_of_freedom + value * value)
    tail = 0.5 * _regularized_incomplete_beta(
        ratio, degrees_of_freedom / 2.0, 0.5
    )
    return 1.0 - tail if value > 0 else tail


def _student_t_quantile(probability: float, degrees_of_freedom: int) -> float:
    if not 0.5 < probability < 1.0:
        raise ValueError("probability must be between 0.5 and 1")
    lower, upper = 0.0, 1.0
    while _student_t_cdf(upper, degrees_of_freedom) < probability:
        upper *= 2.0
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if _student_t_cdf(middle, degrees_of_freedom) < probability:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def _percentile(values: Sequence[float], probability: float) -> float | None:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    position = probability * (len(clean) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    fraction = position - lower
    return clean[lower] * (1.0 - fraction) + clean[upper] * fraction


def _statistical_confidence(
    values: Sequence[float],
    *,
    threshold_r: float,
    confidence_level: float,
    bootstrap_values: Sequence[float] = (),
) -> dict[str, Any]:
    sample = [float(value) for value in values if math.isfinite(float(value))]
    profile: dict[str, Any] = {
        "sample_size": len(sample),
        "threshold_r": threshold_r,
        "confidence_level": confidence_level,
        "mean_r": statistics.fmean(sample) if sample else None,
        "sample_std_r": None,
        "standard_error_r": None,
        "t_statistic": None,
        "degrees_of_freedom": max(0, len(sample) - 1),
        "t_interval_lower_r": None,
        "t_interval_upper_r": None,
        "one_sided_p_value_above_threshold": None,
        "evidence_confidence_above_threshold": None,
        "bootstrap_interval_lower_r": _percentile(
            bootstrap_values, (1.0 - confidence_level) / 2.0
        ),
        "bootstrap_interval_upper_r": _percentile(
            bootstrap_values, 1.0 - (1.0 - confidence_level) / 2.0
        ),
        "bootstrap_replicates": len(bootstrap_values),
    }
    if len(sample) < 2:
        return profile
    deviation = statistics.stdev(sample)
    standard_error = deviation / math.sqrt(len(sample))
    profile["sample_std_r"] = deviation
    profile["standard_error_r"] = standard_error
    if standard_error == 0.0:
        mean = profile["mean_r"]
        profile["t_interval_lower_r"] = mean
        profile["t_interval_upper_r"] = mean
        profile["one_sided_p_value_above_threshold"] = (
            0.0 if mean > threshold_r else 1.0 if mean < threshold_r else 0.5
        )
        profile["evidence_confidence_above_threshold"] = (
            1.0 - profile["one_sided_p_value_above_threshold"]
        )
        return profile
    degrees = len(sample) - 1
    statistic = (profile["mean_r"] - threshold_r) / standard_error
    critical = _student_t_quantile(
        1.0 - (1.0 - confidence_level) / 2.0, degrees
    )
    profile["t_statistic"] = statistic
    profile["t_interval_lower_r"] = profile["mean_r"] - critical * standard_error
    profile["t_interval_upper_r"] = profile["mean_r"] + critical * standard_error
    profile["one_sided_p_value_above_threshold"] = 1.0 - _student_t_cdf(
        statistic, degrees
    )
    profile["evidence_confidence_above_threshold"] = _student_t_cdf(
        statistic, degrees
    )
    return profile


def _verdict_reason(
    verdict: ReliabilityVerdict,
    criteria: Sequence[ReliabilityCriterion],
) -> str:
    names = {
        "REL-OOS-CONFIDENCE": "the expectancy confidence interval",
        "REL-OOS-TRADES": "held-out trade sufficiency",
        "REL-OOS-SESSIONS": "held-out session sufficiency",
        "REL-FOLD-001": "fold consistency",
        "REL-ROBUST-COST": "cost stress",
        "REL-ROBUST-NEIGHBORHOOD": "parameter-neighborhood stability",
        "REL-ROBUST-REGIME": "predeclared regime evidence",
    }
    failed = [
        names.get(item.criterion_id, item.criterion_id)
        for item in criteria if item.outcome is CriterionOutcome.FAIL
    ]
    incomplete = [
        names.get(item.criterion_id, item.criterion_id)
        for item in criteria if item.outcome is CriterionOutcome.INCONCLUSIVE
    ]
    reviewed = [
        names.get(item.criterion_id, item.criterion_id)
        for item in criteria if item.outcome is CriterionOutcome.REVIEW
    ]
    if verdict is ReliabilityVerdict.REJECTED:
        reason = "Rejected because " + ", ".join(failed[:3]) + " failed."
        if incomplete:
            reason += " Additional required evidence remains incomplete."
        return reason
    if verdict is ReliabilityVerdict.INCONCLUSIVE:
        return "Inconclusive because " + ", ".join(incomplete[:3]) + (
            " lack sufficient evidence." if len(incomplete) == 1
            else " lack sufficient evidence."
        )
    if verdict is ReliabilityVerdict.REVIEW_REQUIRED:
        return "Review required because " + ", ".join(reviewed[:3]) + (
            " needs human assessment." if len(reviewed) == 1
            else " need human assessment."
        )
    return "Reliable because every required engine, statistical, stability, and robustness criterion passed."


def _parameter_movement(study: Any) -> float | None:
    coordinates = [
        tuple(fold.selection.selected.candidate.coordinates)
        for fold in getattr(study, "folds", ())
        if getattr(fold, "selection", None) is not None
    ]
    if len(coordinates) < 2:
        return None
    distances = [
        sum(abs(left - right) for left, right in zip(previous, current))
        for previous, current in zip(coordinates, coordinates[1:])
    ]
    return sum(distances) / len(distances)


def evaluate_study_reliability(
    study: Any,
    policy: ReliabilityPolicy,
    *,
    robustness: RobustnessEvidence | None = None,
    symbols: Sequence[str] = (),
    shared_capital_portfolio: bool = False,
    data_integrity_passed: bool = True,
    leakage_passed: bool = True,
    plausibility_review_required: bool = False,
    holdout_summary: Mapping[str, Any] | None = None,
) -> StudyReliabilityReport:
    """Apply v1 deterministically. Rejection outranks insufficiency and review."""

    criteria: list[ReliabilityCriterion] = []
    folds = tuple(getattr(study, "folds", ()))
    reports = [
        getattr(getattr(fold, "test_result", None), "result", None)
        for fold in folds
    ]
    integrity = [
        getattr(result, "engine_integrity_report", None)
        for result in reports
        if result is not None
    ]
    integrity_complete = bool(reports) and len(integrity) == len(reports)
    integrity_passed = integrity_complete and all(
        report is not None and report.status is EngineIntegrityStatus.PASS
        for report in integrity
    )
    criteria.append(_criterion(
        "REL-ENGINE-001",
        CriterionOutcome.PASS if integrity_passed else CriterionOutcome.FAIL,
        {
            "expected_reports": len(reports),
            "present_reports": len(integrity),
            "all_passed": integrity_passed,
        },
        "every TEST result has EngineIntegrityReport PASS",
        "test engine-integrity reports",
    ))

    procedure_state = getattr(getattr(study, "state", None), "value", None) or str(
        getattr(study, "state", "unknown")
    )
    procedure_complete = (
        procedure_state == "completed"
        and getattr(study, "n_folds_completed", 0)
        == getattr(study, "n_folds_expected", -1)
        and getattr(study, "stitched_oos", None) is not None
    )
    criteria.append(_criterion(
        "REL-PROCEDURE-001",
        CriterionOutcome.PASS if procedure_complete else CriterionOutcome.INCONCLUSIVE,
        procedure_state,
        "all declared folds completed and stitched OOS exists",
        "nested procedure state",
    ))

    stitched = getattr(study, "stitched_oos", None)
    n_trades = int(getattr(stitched, "n_trades", 0) or 0)
    expectancy = (
        float(getattr(stitched, "expectancy_r"))
        if stitched is not None and math.isfinite(float(getattr(stitched, "expectancy_r")))
        else None
    )
    session_counts = [
        getattr(getattr(result, "equity", None), "n_sessions", None)
        for result in reports
    ]
    sessions = (
        sum(int(count) for count in session_counts)
        if session_counts and all(count is not None for count in session_counts)
        else 0
    )
    robustness = robustness or RobustnessEvidence()
    statistical_confidence = _statistical_confidence(
        _stitched_r_multiples(study),
        threshold_r=policy.min_stitched_expectancy_r,
        confidence_level=policy.expectancy_confidence_level,
        bootstrap_values=robustness.bootstrap_expectancies_r,
    )
    if policy.version == 1 or not policy.use_expectancy_confidence_interval:
        criteria.append(_criterion(
            "REL-OOS-EXPECTANCY",
            (
                CriterionOutcome.PASS
                if expectancy is not None
                and expectancy >= policy.min_stitched_expectancy_r
                else CriterionOutcome.FAIL
            ),
            expectancy,
            f">= {policy.min_stitched_expectancy_r}R stitched held-out expectancy",
            "stitched_oos.expectancy_r",
        ))
    else:
        criteria.append(_criterion(
            "REL-OOS-EXPECTANCY",
            CriterionOutcome.ADVISORY,
            expectancy,
            "descriptive stitched held-out point estimate",
            "stitched_oos.expectancy_r",
        ))
        lower = statistical_confidence["t_interval_lower_r"]
        upper = statistical_confidence["t_interval_upper_r"]
        confidence_outcome = (
            CriterionOutcome.PASS
            if lower is not None and lower >= policy.min_stitched_expectancy_r
            else CriterionOutcome.FAIL
            if upper is not None and upper < policy.min_stitched_expectancy_r
            else CriterionOutcome.INCONCLUSIVE
        )
        criteria.append(_criterion(
            "REL-OOS-CONFIDENCE",
            confidence_outcome,
            statistical_confidence,
            f"{policy.expectancy_confidence_level:.0%} Student-t interval is "
            f"entirely above {policy.min_stitched_expectancy_r}R to pass or "
            "entirely below it to reject",
            "stitched TEST trade R-multiples",
        ))
    criteria.append(_criterion(
        "REL-OOS-TRADES",
        CriterionOutcome.PASS if n_trades >= policy.min_stitched_oos_trades
        else CriterionOutcome.INCONCLUSIVE,
        n_trades,
        f">= {policy.min_stitched_oos_trades} held-out trades",
        "stitched_oos.n_trades",
    ))
    criteria.append(_criterion(
        "REL-OOS-SESSIONS",
        CriterionOutcome.PASS if sessions >= policy.min_stitched_oos_sessions
        else CriterionOutcome.INCONCLUSIVE,
        sessions,
        f">= {policy.min_stitched_oos_sessions} declared TEST sessions",
        "TEST result equity session counts",
    ))

    fold_expectancies = _fold_expectancies(study)
    positive_fraction = (
        sum(value > 0 for value in fold_expectancies) / len(fold_expectancies)
        if fold_expectancies else None
    )
    criteria.append(_criterion(
        "REL-FOLD-001",
        CriterionOutcome.PASS
        if positive_fraction is not None
        and positive_fraction >= policy.min_positive_fold_fraction
        else CriterionOutcome.FAIL if fold_expectancies else CriterionOutcome.INCONCLUSIVE,
        positive_fraction,
        f">= {policy.min_positive_fold_fraction:.0%} positive TEST folds",
        "per-fold TEST expectancy",
    ))
    validations = _validation_scores(study)
    degradation_values = [
        validation - test
        for validation, test in zip(validations, fold_expectancies)
    ]
    degradation = (
        sum(degradation_values) / len(degradation_values)
        if degradation_values else None
    )
    criteria.append(_criterion(
        "REL-FOLD-002",
        CriterionOutcome.PASS
        if degradation is not None
        and degradation <= policy.max_validation_to_test_degradation_r
        else CriterionOutcome.FAIL if degradation is not None
        else CriterionOutcome.INCONCLUSIVE,
        degradation,
        f"mean validation-to-test degradation <= "
        f"{policy.max_validation_to_test_degradation_r}R",
        "validation selection score", "TEST expectancy",
    ))
    movement = _parameter_movement(study)
    criteria.append(_criterion(
        "REL-SELECTION-001",
        CriterionOutcome.PASS
        if movement is not None
        and movement <= policy.max_parameter_manhattan_movement
        else CriterionOutcome.REVIEW if movement is not None
        else CriterionOutcome.INCONCLUSIVE,
        movement,
        f"mean adjacent-fold parameter movement <= "
        f"{policy.max_parameter_manhattan_movement}",
        "selected candidate coordinates",
    ))
    widths = [
        len(fold.selection.neighborhood_candidate_ids)
        for fold in folds if getattr(fold, "selection", None) is not None
    ]
    min_width = min(widths) if widths else None
    criteria.append(_criterion(
        "REL-SELECTION-002",
        CriterionOutcome.PASS
        if min_width is not None and min_width >= policy.min_plateau_width
        else CriterionOutcome.REVIEW if min_width is not None
        else CriterionOutcome.INCONCLUSIVE,
        min_width,
        f"every selected plateau has >= {policy.min_plateau_width} candidates",
        "selector plateau neighborhood",
    ))

    for identifier, passed, reference in (
        ("REL-DATA-001", data_integrity_passed, "run data-integrity findings"),
        ("REL-LEAKAGE-001", leakage_passed, "feature/split leakage findings"),
    ):
        criteria.append(_criterion(
            identifier,
            CriterionOutcome.PASS if passed else CriterionOutcome.FAIL,
            passed,
            "required evidence passes",
            reference,
        ))
    if plausibility_review_required and policy.plausibility_findings_require_review:
        criteria.append(_criterion(
            "REL-PLAUSIBILITY-001",
            CriterionOutcome.REVIEW,
            True,
            "no unresolved plausibility review findings",
            "candidate validation findings",
        ))

    cost = robustness.cost_stress_expectancy_r
    criteria.append(_criterion(
        "REL-ROBUST-COST",
        CriterionOutcome.ADVISORY
        if not policy.require_cost_stress_survival
        else
        CriterionOutcome.PASS
        if cost is not None and cost >= policy.min_cost_stress_expectancy_r
        else CriterionOutcome.FAIL if cost is not None
        else CriterionOutcome.INCONCLUSIVE,
        cost,
        f"declared cost stress expectancy >= {policy.min_cost_stress_expectancy_r}R",
        "robustness cost scenarios",
    ))
    neighbors = robustness.neighbor_expectancies_r
    neighbor_fraction = (
        sum(value >= 0 for value in neighbors) / len(neighbors)
        if neighbors else None
    )
    criteria.append(_criterion(
        "REL-ROBUST-NEIGHBORHOOD",
        CriterionOutcome.ADVISORY
        if not policy.require_parameter_neighborhood
        else
        CriterionOutcome.PASS
        if neighbor_fraction is not None
        and neighbor_fraction >= policy.min_profitable_neighbor_fraction
        and not robustness.sign_change_detected
        and not robustness.cliff_detected
        else CriterionOutcome.FAIL if neighbor_fraction is not None
        else CriterionOutcome.INCONCLUSIVE,
        {
            "profitable_fraction": neighbor_fraction,
            "sign_change": robustness.sign_change_detected,
            "cliff": robustness.cliff_detected,
        },
        f">= {policy.min_profitable_neighbor_fraction:.0%} non-negative neighbors "
        "with no sign change or cliff",
        "deterministic parameter neighborhood",
    ))
    regime_ready = (
        robustness.regimes_predeclared is True
        and robustness.regime_count is not None
        and robustness.regime_count >= policy.min_regime_count
        and robustness.max_single_regime_return_concentration is not None
        and robustness.max_single_regime_return_concentration
        <= policy.max_single_regime_return_concentration
    )
    criteria.append(_criterion(
        "REL-ROBUST-REGIME",
        CriterionOutcome.ADVISORY
        if not policy.require_regime_evidence
        else
        CriterionOutcome.PASS if regime_ready
        else CriterionOutcome.FAIL
        if robustness.regimes_predeclared is False
        else CriterionOutcome.INCONCLUSIVE,
        {
            "predeclared": robustness.regimes_predeclared,
            "count": robustness.regime_count,
            "max_concentration": robustness.max_single_regime_return_concentration,
        },
        f"predeclared >= {policy.min_regime_count} regimes and max concentration "
        f"<= {policy.max_single_regime_return_concentration:.0%}",
        "regime robustness evidence",
    ))
    subsampling_present = (
        robustness.subsample_seed is not None
        and robustness.subsample_identity is not None
        and bool(robustness.bootstrap_expectancies_r)
    )
    criteria.append(_criterion(
        "REL-ROBUST-SUBSAMPLE",
        CriterionOutcome.ADVISORY
        if not policy.require_seeded_subsampling
        else
        CriterionOutcome.ADVISORY
        if not subsampling_present and policy.subsampling_is_advisory
        else CriterionOutcome.PASS if subsampling_present
        else CriterionOutcome.INCONCLUSIVE,
        {
            "seed": robustness.subsample_seed,
            "sample_identity": robustness.subsample_identity,
            "replicates": len(robustness.bootstrap_expectancies_r),
        },
        "seeded reproducible subsampling evidence is present",
        "bootstrap/subsampling provenance",
    ))
    criteria.append(_criterion(
        "REL-MULTIPLE-TESTING",
        CriterionOutcome.ADVISORY,
        {
            "n_candidates_evaluated": robustness.n_candidates_evaluated,
            "diagnostic": robustness.multiple_testing_diagnostic,
        },
        "candidate count retained; no unsupported adjusted statistic is claimed",
        "optimization batch counts",
    ))

    multi_symbol = len({symbol.upper() for symbol in symbols}) > 1
    portfolio_ok = not multi_symbol or shared_capital_portfolio
    criteria.append(_criterion(
        "REL-PORTFOLIO-001",
        CriterionOutcome.ADVISORY
        if not policy.require_shared_capital_for_multi_symbol
        else CriterionOutcome.PASS if portfolio_ok else CriterionOutcome.INCONCLUSIVE,
        {
            "symbols": sorted({symbol.upper() for symbol in symbols}),
            "shared_capital": shared_capital_portfolio,
        },
        "multi-symbol claims use one shared-capital PortfolioBacktestEngine result",
        "portfolio execution provenance",
    ))

    outcomes = {item.outcome for item in criteria}
    if CriterionOutcome.FAIL in outcomes:
        verdict = ReliabilityVerdict.REJECTED
    elif CriterionOutcome.INCONCLUSIVE in outcomes:
        verdict = ReliabilityVerdict.INCONCLUSIVE
    elif CriterionOutcome.REVIEW in outcomes:
        verdict = ReliabilityVerdict.REVIEW_REQUIRED
    else:
        verdict = ReliabilityVerdict.RELIABLE
    verdict_reason = _verdict_reason(verdict, criteria)

    return StudyReliabilityReport(
        verdict=verdict,
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        engine_integrity_complete=integrity_complete,
        procedure_state=procedure_state,
        stitched_oos_summary={
            "n_trades": n_trades,
            "session_span": sessions,
            "expectancy_r": expectancy,
        },
        fold_stability_summary={
            "fold_expectancies_r": tuple(fold_expectancies),
            "positive_fold_fraction": positive_fraction,
            "mean_validation_to_test_degradation_r": degradation,
            "mean_parameter_movement": movement,
        },
        robustness_summary={
            **robustness.model_dump(mode="json"),
            "provenance_hash": robustness.provenance_hash,
        },
        holdout_summary=dict(holdout_summary or {}),
        statistical_confidence=statistical_confidence,
        verdict_reason=verdict_reason,
        criteria=tuple(criteria),
    )
