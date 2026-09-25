"""One-pass validation gates and terminal-state decisions for research runs.

Validation is deliberately separate from metric calculation.  The simulator
reports what happened; this module decides whether those numbers are internally
consistent, sufficiently evidenced, and plausible enough to trust.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vibe.backtester.analysis.metrics import BacktestResult
from vibe.research_pipeline.contracts import (
    RunEvidence,
    Severity,
    ValidationCategory,
    ValidationFinding,
)
from vibe.research_pipeline.features.leakage import LeakageReport
from vibe.research_pipeline.lifecycle import RunState

__all__ = [
    "AcceptanceDirection",
    "AcceptanceRule",
    "DataIntegritySummary",
    "MetricValidator",
    "ValidationInput",
    "ValidationProfile",
    "ValidationReport",
    "ValidationScope",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ValidationScope(str, Enum):
    """Level at which a result is being judged."""

    RUN = "run"
    SWEEP_ROW = "sweep_row"
    CANDIDATE = "candidate"
    PROMOTION = "promotion"

    @property
    def applies_candidate_gates(self) -> bool:
        return self in {ValidationScope.CANDIDATE, ValidationScope.PROMOTION}


class AcceptanceDirection(str, Enum):
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


class AcceptanceRule(_Frozen):
    """Pre-registered strategy-quality criterion.

    A miss is an unsuccessful hypothesis, not corrupt arithmetic.  It is
    therefore recorded as advisory and never changes a clean run's state.
    """

    metric_key: str = Field(..., min_length=1)
    direction: AcceptanceDirection
    threshold: float
    description: str = Field(..., min_length=1)

    def passes(self, value: float) -> bool:
        if self.direction is AcceptanceDirection.AT_LEAST:
            return value >= self.threshold
        return value <= self.threshold


class ValidationProfile(_Frozen):
    """Declared bounds applied by :class:`MetricValidator`."""

    name: str = Field("default", min_length=1)
    min_sessions: int = Field(1, ge=0)
    min_trades: int = Field(1, ge=0)
    declared_margin: float = Field(0.0, ge=0.0)
    max_gross_leverage: float = Field(1.0, gt=0.0)
    expected_execution_model_version: Optional[int] = Field(None, ge=1)
    require_all_accounting_checks: bool = True
    require_costs_when_trading: bool = True
    high_win_rate: float = Field(0.55, ge=0.0, le=1.0)
    ambiguous_exit_rate: float = Field(0.05, ge=0.0, le=1.0)
    required_leakage_checks: tuple[str, ...] = (
        "truncation_equivalence",
        "prefix_invariance",
        "future_perturbation",
        "feature_availability",
        "orb_boundary",
        "split_contamination",
    )
    acceptance_rules: tuple[AcceptanceRule, ...] = ()


class DataIntegritySummary(_Frozen):
    """Facts measured from the exact bars consumed by the run."""

    expected_sessions: int = Field(..., ge=0)
    observed_sessions: int = Field(..., ge=0)
    bar_count: int = Field(..., ge=0)
    duplicate_timestamps: int = Field(0, ge=0)
    non_monotonic_timestamps: int = Field(0, ge=0)
    invalid_ohlc_bars: int = Field(0, ge=0)
    non_positive_price_bars: int = Field(0, ge=0)
    anomalous_sessions: int = Field(0, ge=0)


@dataclass(frozen=True)
class ValidationInput:
    """All evidence needed to make one terminal-state decision."""

    result: BacktestResult
    evidence: RunEvidence
    data_integrity: DataIntegritySummary
    leakage_report: LeakageReport
    registry_hash_at_execution: str
    current_registry_hash: str
    scope: ValidationScope = ValidationScope.RUN


class ValidationReport(_Frozen):
    """Complete one-pass diagnosis and the state implied by it."""

    profile_name: str
    scope: ValidationScope
    target_state: RunState
    findings: tuple[ValidationFinding, ...] = ()
    checked_categories: tuple[ValidationCategory, ...]
    registry_hash_at_execution: str
    current_registry_hash: str
    leakage_passed: bool

    @model_validator(mode="after")
    def _state_matches_findings(self) -> "ValidationReport":
        has_blocking = any(f.severity is Severity.BLOCKING for f in self.findings)
        has_review = any(f.severity is Severity.REVIEW for f in self.findings)
        has_inconclusive = any(
            f.category is ValidationCategory.DATASET_SUFFICIENCY
            for f in self.findings
        )
        if has_blocking and self.target_state is not RunState.VALIDATION_FAILED:
            raise ValueError("Blocking findings require VALIDATION_FAILED")
        if not has_blocking and has_inconclusive and self.target_state is not RunState.INCONCLUSIVE:
            raise ValueError("Dataset insufficiency requires INCONCLUSIVE")
        if (
            not has_blocking
            and not has_inconclusive
            and has_review
            and self.target_state is not RunState.REVIEW_REQUIRED
        ):
            raise ValueError("Review findings require REVIEW_REQUIRED")
        if not (has_blocking or has_inconclusive or has_review):
            if self.target_state is not RunState.COMPLETED:
                raise ValueError("A clean validation report must complete")
        return self

    @property
    def passed(self) -> bool:
        return self.target_state is RunState.COMPLETED


def _finding(
    code: str,
    category: ValidationCategory,
    severity: Severity,
    message: str,
    *,
    metric_key: Optional[str] = None,
    observed: Any = None,
    expected: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
) -> ValidationFinding:
    return ValidationFinding(
        code=code,
        category=category,
        severity=severity,
        message=message,
        metric_key=metric_key,
        observed=observed,
        expected=expected,
        context=context or {},
    )


def _metric_values(result: BacktestResult) -> dict[str, float]:
    overall = result.overall
    equity = result.equity
    values = {
        name: float(getattr(overall, name))
        for name in (
            "n_trades",
            "win_rate",
            "avg_win_r",
            "avg_loss_r",
            "expectancy_r",
            "max_win_r",
            "max_loss_r",
            "top10_pct",
            "skewness",
            "max_losing_streak",
            "total_pnl",
            "winning_trades",
            "losing_trades",
            "breakeven_trades",
            "r_sample_size",
            "dropped_trade_count",
            "gross_pnl",
            "total_costs",
            "calculation_version",
        )
    }
    values.update(
        {
            name: float(getattr(equity, name))
            for name in (
                "total_return",
                "annualized_return",
                "sharpe_ratio",
                "max_drawdown",
                "max_drawdown_duration_days",
                "bars_per_session",
                "n_sessions",
                "calculation_version",
            )
        }
    )
    values.update({key: float(value) for key, value in result.execution_diagnostics.items()})
    return values


class MetricValidator:
    """Collect every finding before deciding the run's terminal state."""

    _CATEGORIES = (
        ValidationCategory.HARD_INVARIANT,
        ValidationCategory.ACCOUNTING,
        ValidationCategory.EXECUTION_REALISM,
        ValidationCategory.DATA_INTEGRITY,
        ValidationCategory.SPLIT_INTEGRITY,
        ValidationCategory.LOOK_AHEAD,
        ValidationCategory.DATASET_SUFFICIENCY,
        ValidationCategory.PLAUSIBILITY,
        ValidationCategory.RESEARCH_ACCEPTANCE,
    )

    def __init__(self, profile: ValidationProfile) -> None:
        self.profile = profile

    def validate(self, validation_input: ValidationInput) -> ValidationReport:
        findings: list[ValidationFinding] = []
        self._check_mathematics(validation_input, findings)
        self._check_cross_metrics(validation_input, findings)
        self._check_accounting(validation_input, findings)
        self._check_execution(validation_input, findings)
        self._check_data(validation_input, findings)
        self._check_leakage(validation_input, findings)
        self._check_sufficiency(validation_input, findings)
        if validation_input.scope.applies_candidate_gates:
            self._check_plausibility(validation_input, findings)
            self._check_acceptance(validation_input, findings)

        if any(f.severity is Severity.BLOCKING for f in findings):
            target = RunState.VALIDATION_FAILED
        elif any(
            f.category is ValidationCategory.DATASET_SUFFICIENCY for f in findings
        ):
            target = RunState.INCONCLUSIVE
        elif any(f.severity is Severity.REVIEW for f in findings):
            target = RunState.REVIEW_REQUIRED
        else:
            target = RunState.COMPLETED

        checked_categories = list(self._CATEGORIES)
        if not validation_input.scope.applies_candidate_gates:
            checked_categories.remove(ValidationCategory.PLAUSIBILITY)
            checked_categories.remove(ValidationCategory.RESEARCH_ACCEPTANCE)
        observed_checks = {
            finding.check for finding in validation_input.leakage_report.findings
        }
        leakage_evidence_complete = set(
            self.profile.required_leakage_checks
        ) <= observed_checks
        return ValidationReport(
            profile_name=self.profile.name,
            scope=validation_input.scope,
            target_state=target,
            findings=tuple(findings),
            checked_categories=tuple(checked_categories),
            registry_hash_at_execution=validation_input.registry_hash_at_execution,
            current_registry_hash=validation_input.current_registry_hash,
            leakage_passed=(
                validation_input.leakage_report.passed
                and leakage_evidence_complete
                and validation_input.registry_hash_at_execution
                == validation_input.current_registry_hash
            ),
        )

    def _check_mathematics(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        for key, observed in _metric_values(value.result).items():
            if not math.isfinite(observed):
                findings.append(
                    _finding(
                        "MATH-001",
                        ValidationCategory.HARD_INVARIANT,
                        Severity.BLOCKING,
                        f"Published metric {key!r} is not finite.",
                        metric_key=key,
                        observed=observed,
                        expected="a finite number",
                    )
                )
        win_rate = value.result.overall.win_rate
        if not 0.0 <= win_rate <= 1.0:
            findings.append(
                _finding(
                    "MATH-002",
                    ValidationCategory.HARD_INVARIANT,
                    Severity.BLOCKING,
                    "Win rate lies outside its declared fractional unit.",
                    metric_key="win_rate",
                    observed=win_rate,
                    expected="0 <= win_rate <= 1",
                )
            )
        drawdown = value.result.equity.max_drawdown
        if not -1.0 <= drawdown <= 0.0:
            findings.append(
                _finding(
                    "MATH-003",
                    ValidationCategory.HARD_INVARIANT,
                    Severity.BLOCKING,
                    "Maximum drawdown lies outside its declared negative-fraction unit.",
                    metric_key="max_drawdown",
                    observed=drawdown,
                    expected="-1 <= max_drawdown <= 0",
                )
            )

    def _check_cross_metrics(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        metrics = value.result.overall
        actual = len(value.result.trades)
        if metrics.n_trades != actual or value.evidence.trade_count != actual:
            findings.append(
                _finding(
                    "CROSS-001",
                    ValidationCategory.HARD_INVARIANT,
                    Severity.BLOCKING,
                    "Trade census disagrees across metrics, ledger, and evidence.",
                    metric_key="n_trades",
                    observed={
                        "metric": metrics.n_trades,
                        "ledger": actual,
                        "evidence": value.evidence.trade_count,
                    },
                    expected="all trade counts equal",
                )
            )
        partition = (
            metrics.winning_trades
            + metrics.losing_trades
            + metrics.breakeven_trades
        )
        if partition != metrics.r_sample_size:
            findings.append(
                _finding(
                    "CROSS-002",
                    ValidationCategory.HARD_INVARIANT,
                    Severity.BLOCKING,
                    "Win/loss/breakeven counts do not partition the R sample.",
                    metric_key="r_sample_size",
                    observed=partition,
                    expected=str(metrics.r_sample_size),
                )
            )
        if metrics.dropped_trade_count != metrics.n_trades - metrics.r_sample_size:
            findings.append(
                _finding(
                    "CROSS-003",
                    ValidationCategory.HARD_INVARIANT,
                    Severity.BLOCKING,
                    "Dropped-trade accounting does not explain the R-sample gap.",
                    metric_key="dropped_trade_count",
                    observed=metrics.dropped_trade_count,
                    expected=str(metrics.n_trades - metrics.r_sample_size),
                )
            )
        if metrics.dropped_trade_count:
            findings.append(
                _finding(
                    "CROSS-004",
                    ValidationCategory.HARD_INVARIANT,
                    Severity.BLOCKING,
                    "Trades with unusable initial risk were dropped from R metrics.",
                    metric_key="dropped_trade_count",
                    observed=metrics.dropped_trade_count,
                    expected="0",
                )
            )

    def _check_accounting(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        metrics = value.result.overall
        evidence = value.evidence
        if not math.isclose(
            metrics.gross_pnl - metrics.total_costs,
            metrics.total_pnl,
            rel_tol=1e-9,
            abs_tol=0.01,
        ):
            findings.append(
                _finding(
                    "ACC-001",
                    ValidationCategory.ACCOUNTING,
                    Severity.BLOCKING,
                    "Metric gross P&L minus costs does not equal net P&L.",
                    observed=metrics.total_pnl,
                    expected=f"{metrics.gross_pnl - metrics.total_costs:.6f}",
                )
            )
        comparisons = {
            "gross_pnl": (metrics.gross_pnl, evidence.gross_pnl),
            "total_costs": (metrics.total_costs, evidence.total_costs),
            "net_pnl": (metrics.total_pnl, evidence.net_pnl),
        }
        for key, (metric_value, evidence_value) in comparisons.items():
            if not math.isclose(metric_value, evidence_value, rel_tol=1e-9, abs_tol=0.01):
                findings.append(
                    _finding(
                        "ACC-002",
                        ValidationCategory.ACCOUNTING,
                        Severity.BLOCKING,
                        f"{key} disagrees between metrics and durable evidence.",
                        metric_key=key,
                        observed=metric_value,
                        expected=str(evidence_value),
                    )
                )
        diagnostic_comparisons = {
            "min_cash": evidence.min_cash,
            "max_gross_exposure_ratio": evidence.max_observed_leverage,
            "total_costs": evidence.total_costs,
        }
        for key, evidence_value in diagnostic_comparisons.items():
            observed = value.result.execution_diagnostics.get(key)
            if observed is None or not math.isclose(
                observed, evidence_value, rel_tol=1e-9, abs_tol=0.01
            ):
                findings.append(
                    _finding(
                        "ACC-004",
                        ValidationCategory.ACCOUNTING,
                        Severity.BLOCKING,
                        f"{key} disagrees between execution diagnostics and evidence.",
                        metric_key=key,
                        observed=observed,
                        expected=str(evidence_value),
                    )
                )
        failures = value.result.execution_diagnostics.get("accounting_failures")
        if failures is None or failures != 0:
            findings.append(
                _finding(
                    "ACC-003",
                    ValidationCategory.ACCOUNTING,
                    Severity.BLOCKING,
                    "Execution reconciliation did not report a clean pass.",
                    observed=failures,
                    expected="accounting_failures == 0",
                )
            )

    def _check_execution(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        diagnostics = value.result.execution_diagnostics
        min_cash = diagnostics.get("min_cash")
        if min_cash is None or min_cash < -self.profile.declared_margin - 0.01:
            findings.append(
                _finding(
                    "EXEC-001",
                    ValidationCategory.EXECUTION_REALISM,
                    Severity.BLOCKING,
                    "Cash fell below the declared margin allowance.",
                    metric_key="min_cash",
                    observed=min_cash,
                    expected=f">= {-self.profile.declared_margin}",
                )
            )
        leverage = diagnostics.get("max_gross_exposure_ratio")
        if leverage is None or leverage > self.profile.max_gross_leverage + 1e-9:
            findings.append(
                _finding(
                    "EXEC-002",
                    ValidationCategory.EXECUTION_REALISM,
                    Severity.BLOCKING,
                    "Observed leverage exceeded the declared ceiling.",
                    metric_key="max_gross_exposure_ratio",
                    observed=leverage,
                    expected=f"<= {self.profile.max_gross_leverage}",
                )
            )
        applicable = diagnostics.get("accounting_checks_applicable")
        if self.profile.require_all_accounting_checks and applicable != 3:
            findings.append(
                _finding(
                    "EXEC-003",
                    ValidationCategory.EXECUTION_REALISM,
                    Severity.BLOCKING,
                    "Not all execution reconciliation checks were applicable.",
                    observed=applicable,
                    expected="3 checks; intraday runs must be flat at end",
                )
            )
        version = diagnostics.get("execution_model_version")
        expected_version = self.profile.expected_execution_model_version
        if expected_version is not None and version != float(expected_version):
            findings.append(
                _finding(
                    "EXEC-004",
                    ValidationCategory.EXECUTION_REALISM,
                    Severity.BLOCKING,
                    "Execution-model version differs from the validation profile.",
                    observed=version,
                    expected=str(expected_version),
                )
            )

    def _check_data(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        data = value.data_integrity
        checks = (
            ("DATA-001", data.observed_sessions, data.expected_sessions, "observed sessions"),
            ("DATA-002", data.duplicate_timestamps, 0, "duplicate timestamps"),
            ("DATA-003", data.non_monotonic_timestamps, 0, "non-monotonic timestamps"),
            ("DATA-004", data.invalid_ohlc_bars, 0, "bars with high below low"),
            ("DATA-005", data.non_positive_price_bars, 0, "bars with non-positive prices"),
            ("DATA-006", data.anomalous_sessions, 0, "sessions with anomalous bar counts"),
            (
                "DATA-007",
                value.result.equity.n_sessions,
                data.observed_sessions,
                "metric sessions disagreeing with observed sessions",
            ),
        )
        for code, observed, expected, label in checks:
            if observed != expected:
                findings.append(
                    _finding(
                        code,
                        ValidationCategory.DATA_INTEGRITY,
                        Severity.BLOCKING,
                        f"Data integrity check failed: {label}.",
                        observed=observed,
                        expected=str(expected),
                    )
                )
        if data.observed_sessions and data.bar_count == 0:
            findings.append(
                _finding(
                    "DATA-008",
                    ValidationCategory.DATA_INTEGRITY,
                    Severity.BLOCKING,
                    "Observed sessions contain no bars.",
                    observed=data.bar_count,
                    expected="> 0",
                )
            )

    def _check_leakage(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        observed_checks = {finding.check for finding in value.leakage_report.findings}
        for missing in sorted(set(self.profile.required_leakage_checks) - observed_checks):
            findings.append(
                _finding(
                    "LEAK-003",
                    (
                        ValidationCategory.SPLIT_INTEGRITY
                        if missing == "split_contamination"
                        else ValidationCategory.LOOK_AHEAD
                    ),
                    Severity.BLOCKING,
                    f"Required leakage check {missing!r} has no recorded evidence.",
                    context={"check": missing},
                )
            )
        if value.registry_hash_at_execution != value.current_registry_hash:
            findings.append(
                _finding(
                    "LEAK-001",
                    ValidationCategory.LOOK_AHEAD,
                    Severity.BLOCKING,
                    "Feature registry changed after the result was produced.",
                    observed=value.registry_hash_at_execution,
                    expected=value.current_registry_hash,
                )
            )
        for leakage in value.leakage_report.failures:
            category = (
                ValidationCategory.SPLIT_INTEGRITY
                if leakage.check == "split_contamination"
                else ValidationCategory.LOOK_AHEAD
            )
            findings.append(
                _finding(
                    "LEAK-002",
                    category,
                    Severity.BLOCKING,
                    leakage.detail,
                    metric_key=leakage.feature,
                    observed=leakage.full_value,
                    expected=(
                        None
                        if leakage.restricted_value is None
                        else str(leakage.restricted_value)
                    ),
                    context={
                        "check": leakage.check,
                        "first_divergence": (
                            None
                            if leakage.first_divergence is None
                            else str(leakage.first_divergence)
                        ),
                    },
                )
            )

    def _check_sufficiency(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        trades = value.result.overall.n_trades
        sessions = value.data_integrity.observed_sessions
        if trades < self.profile.min_trades:
            findings.append(
                _finding(
                    "SUFF-001",
                    ValidationCategory.DATASET_SUFFICIENCY,
                    Severity.ADVISORY,
                    "The run produced too few trades to support a conclusion.",
                    metric_key="n_trades",
                    observed=trades,
                    expected=f">= {self.profile.min_trades}",
                )
            )
        if sessions < self.profile.min_sessions:
            findings.append(
                _finding(
                    "SUFF-002",
                    ValidationCategory.DATASET_SUFFICIENCY,
                    Severity.ADVISORY,
                    "The run contains too few sessions for the requested analysis.",
                    metric_key="n_sessions",
                    observed=sessions,
                    expected=f">= {self.profile.min_sessions}",
                )
            )

    def _check_plausibility(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        metrics = value.result.overall
        if (
            metrics.win_rate > self.profile.high_win_rate
            and metrics.avg_win_r >= abs(metrics.avg_loss_r)
        ):
            findings.append(
                _finding(
                    "PLAUS-001",
                    ValidationCategory.PLAUSIBILITY,
                    Severity.REVIEW,
                    "High win rate combined with non-inferior average win requires review.",
                    metric_key="win_rate",
                    observed=metrics.win_rate,
                    expected=f"<= {self.profile.high_win_rate} unless payoff asymmetry explains it",
                )
            )
        ambiguous = value.result.execution_diagnostics.get("ambiguous_exit_bars", 0.0)
        rate = ambiguous / metrics.n_trades if metrics.n_trades else 0.0
        if rate > self.profile.ambiguous_exit_rate:
            findings.append(
                _finding(
                    "PLAUS-002",
                    ValidationCategory.PLAUSIBILITY,
                    Severity.REVIEW,
                    "A material share of trades depends on ambiguous intrabar ordering.",
                    metric_key="ambiguous_exit_rate",
                    observed=rate,
                    expected=f"<= {self.profile.ambiguous_exit_rate}",
                )
            )
        if (
            self.profile.require_costs_when_trading
            and metrics.n_trades > 0
            and metrics.total_costs <= 0
        ):
            findings.append(
                _finding(
                    "PLAUS-003",
                    ValidationCategory.PLAUSIBILITY,
                    Severity.REVIEW,
                    "A traded candidate reports no transaction costs.",
                    metric_key="total_costs",
                    observed=metrics.total_costs,
                    expected="> 0 when n_trades > 0",
                )
            )

    def _check_acceptance(
        self, value: ValidationInput, findings: list[ValidationFinding]
    ) -> None:
        metrics: Mapping[str, float] = _metric_values(value.result)
        for index, rule in enumerate(self.profile.acceptance_rules, start=1):
            observed = metrics.get(rule.metric_key)
            if observed is None:
                findings.append(
                    _finding(
                        f"ACCEPT-{index:03d}",
                        ValidationCategory.RESEARCH_ACCEPTANCE,
                        Severity.ADVISORY,
                        f"Acceptance metric {rule.metric_key!r} is unavailable.",
                        metric_key=rule.metric_key,
                        expected=rule.description,
                    )
                )
            elif not rule.passes(observed):
                findings.append(
                    _finding(
                        f"ACCEPT-{index:03d}",
                        ValidationCategory.RESEARCH_ACCEPTANCE,
                        Severity.ADVISORY,
                        f"Research acceptance criterion failed: {rule.description}",
                        metric_key=rule.metric_key,
                        observed=observed,
                        expected=f"{rule.direction.value} {rule.threshold}",
                    )
                )
