"""Synthetic fixtures for every P6 validation category."""

from __future__ import annotations

from datetime import date

import pandas as pd

from vibe.backtester.analysis.metrics import (
    BacktestResult,
    ConvexityMetrics,
    EquityMetrics,
)
from vibe.research_pipeline.contracts import (
    RetentionProfile,
    RunEvidence,
    ValidationCategory,
)
from vibe.research_pipeline.features.leakage import LeakageFinding, LeakageReport
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.validation import (
    AcceptanceDirection,
    AcceptanceRule,
    DataIntegritySummary,
    MetricValidator,
    ValidationInput,
    ValidationProfile,
    ValidationScope,
)


def _result(**metric_overrides) -> BacktestResult:
    metric_defaults = dict(
        n_trades=2,
        win_rate=0.5,
        avg_win_r=1.0,
        avg_loss_r=-1.0,
        expectancy_r=0.0,
        max_win_r=1.0,
        max_loss_r=-1.0,
        top10_pct=100.0,
        skewness=0.0,
        max_losing_streak=1,
        total_pnl=100.0,
        stop_wins=1,
        stop_losses=1,
        eod_wins=0,
        eod_losses=0,
        r_multiples=[1.0, -1.0],
        first_date="2026-01-02",
        last_date="2026-01-03",
        winning_trades=1,
        losing_trades=1,
        breakeven_trades=0,
        r_sample_size=2,
        dropped_trade_count=0,
        gross_pnl=110.0,
        total_costs=10.0,
    )
    metric_defaults.update(metric_overrides)
    overall = ConvexityMetrics(**metric_defaults)
    equity = EquityMetrics(
        total_return=0.1,
        annualized_return=0.1,
        sharpe_ratio=1.0,
        max_drawdown=-0.1,
        max_drawdown_duration_days=2,
        equity_curve=pd.Series(
            [1_000.0, 1_100.0],
            index=pd.to_datetime(["2026-01-02", "2026-01-03"]),
        ),
        drawdown_curve=pd.Series([0.0, -0.1]),
        bars_per_session=78.0,
        n_sessions=10,
    )
    return BacktestResult(
        overall=overall,
        by_year={},
        equity=equity,
        trades=[object(), object()],
        regime_breakdown={},
        symbol="QQQ",
        start_date="2026-01-02",
        end_date="2026-01-15",
        ruleset_name="orb",
        ruleset_version="1",
        execution_diagnostics={
            "ambiguous_exit_bars": 0.0,
            "min_cash": 100.0,
            "max_gross_exposure_ratio": 0.5,
            "total_costs": 10.0,
            "execution_model_version": 4.0,
            "accounting_failures": 0.0,
            "accounting_checks_applicable": 3.0,
        },
    )


def _evidence(**overrides) -> RunEvidence:
    values = dict(
        trade_count=2,
        trade_ledger_sha256="1" * 64,
        equity_curve_sha256="2" * 64,
        equity_points=2,
        first_session=date(2026, 1, 2),
        last_session=date(2026, 1, 15),
        starting_equity=1_000.0,
        ending_equity=1_100.0,
        gross_pnl=110.0,
        total_costs=10.0,
        net_pnl=100.0,
        max_observed_leverage=0.5,
        min_cash=100.0,
        retention_profile=RetentionProfile.SUMMARY,
    )
    values.update(overrides)
    return RunEvidence(**values)


def _input(
    *,
    result: BacktestResult | None = None,
    evidence: RunEvidence | None = None,
    data: DataIntegritySummary | None = None,
    leakage: LeakageReport | None = None,
    execution_hash: str = "a" * 64,
    current_hash: str = "a" * 64,
    scope: ValidationScope = ValidationScope.RUN,
) -> ValidationInput:
    return ValidationInput(
        result=result or _result(),
        evidence=evidence or _evidence(),
        data_integrity=data
        or DataIntegritySummary(
            expected_sessions=10,
            observed_sessions=10,
            bar_count=780,
        ),
        leakage_report=leakage
        or LeakageReport(
            tuple(
                LeakageFinding(
                    check=check,
                    feature="atr_14",
                    passed=True,
                    detail="stable",
                )
                for check in (
                    "truncation_equivalence",
                    "prefix_invariance",
                    "future_perturbation",
                    "feature_availability",
                    "orb_boundary",
                    "split_contamination",
                )
            )
        ),
        registry_hash_at_execution=execution_hash,
        current_registry_hash=current_hash,
        scope=scope,
    )


def _validate(value: ValidationInput, **profile_overrides):
    return MetricValidator(
        ValidationProfile(
            expected_execution_model_version=4,
            **profile_overrides,
        )
    ).validate(value)


def test_clean_run_completes_after_all_categories_are_checked():
    report = _validate(_input(scope=ValidationScope.CANDIDATE))
    assert report.target_state is RunState.COMPLETED
    assert report.findings == ()
    assert set(report.checked_categories) == set(ValidationCategory)


def test_mathematical_and_cross_metric_failures_are_collected_in_one_pass():
    result = _result(
        win_rate=1.5,
        r_sample_size=3,
        dropped_trade_count=1,
    )
    result.equity.sharpe_ratio = float("inf")
    report = _validate(_input(result=result))
    assert report.target_state is RunState.VALIDATION_FAILED
    assert {"MATH-001", "MATH-002", "CROSS-002", "CROSS-003", "CROSS-004"} <= {
        finding.code for finding in report.findings
    }


def test_accounting_reconciliation_failure_blocks_completion():
    result = _result()
    result.execution_diagnostics["accounting_failures"] = 1.0
    report = _validate(_input(result=result))
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "ACC-003" in {finding.code for finding in report.findings}


def test_execution_limit_failure_blocks_completion():
    result = _result()
    result.execution_diagnostics["max_gross_exposure_ratio"] = 1.5
    report = _validate(_input(result=result), max_gross_leverage=1.0)
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "EXEC-002" in {finding.code for finding in report.findings}


def test_data_integrity_failures_are_blocking_and_all_reported():
    data = DataIntegritySummary(
        expected_sessions=10,
        observed_sessions=9,
        bar_count=700,
        duplicate_timestamps=2,
        invalid_ohlc_bars=1,
    )
    report = _validate(_input(data=data))
    assert report.target_state is RunState.VALIDATION_FAILED
    assert {"DATA-001", "DATA-002", "DATA-004"} <= {
        finding.code for finding in report.findings
    }


def test_leakage_failure_and_registry_drift_both_block_completion():
    leakage = LeakageReport(
        (
            LeakageFinding(
                check="split_contamination",
                feature="train_vs_test",
                passed=False,
                detail="session 2026-01-05 overlaps",
            ),
        )
    )
    report = _validate(
        _input(
            leakage=leakage,
            execution_hash="a" * 64,
            current_hash="b" * 64,
        )
    )
    assert report.target_state is RunState.VALIDATION_FAILED
    assert {"LEAK-001", "LEAK-002"} <= {
        finding.code for finding in report.findings
    }
    assert any(
        finding.category is ValidationCategory.SPLIT_INTEGRITY
        for finding in report.findings
    )


def test_missing_leakage_evidence_blocks_completion():
    report = _validate(_input(leakage=LeakageReport()))
    assert report.target_state is RunState.VALIDATION_FAILED
    assert {finding.code for finding in report.findings} == {"LEAK-003"}


def test_plausibility_only_applies_at_candidate_level():
    result = _result(win_rate=0.75, avg_win_r=1.2, avg_loss_r=-1.0)
    result.execution_diagnostics["ambiguous_exit_bars"] = 1.0

    sweep_row = _validate(_input(result=result, scope=ValidationScope.SWEEP_ROW))
    candidate = _validate(_input(result=result, scope=ValidationScope.CANDIDATE))

    assert sweep_row.target_state is RunState.COMPLETED
    assert candidate.target_state is RunState.REVIEW_REQUIRED
    assert {"PLAUS-001", "PLAUS-002"} <= {
        finding.code for finding in candidate.findings
    }


def test_acceptance_failure_is_advisory_and_run_still_completes():
    report = _validate(
        _input(scope=ValidationScope.CANDIDATE),
        acceptance_rules=(
            AcceptanceRule(
                metric_key="expectancy_r",
                direction=AcceptanceDirection.AT_LEAST,
                threshold=0.25,
                description="OOS expectancy must reach 0.25R",
            ),
        ),
    )
    assert report.target_state is RunState.COMPLETED
    assert [finding.code for finding in report.findings] == ["ACCEPT-001"]


def test_zero_trades_is_inconclusive_not_failed():
    result = _result(
        n_trades=0,
        win_rate=0.0,
        avg_win_r=0.0,
        avg_loss_r=0.0,
        expectancy_r=0.0,
        max_win_r=0.0,
        max_loss_r=0.0,
        total_pnl=0.0,
        winning_trades=0,
        losing_trades=0,
        r_sample_size=0,
        gross_pnl=0.0,
        total_costs=0.0,
        r_multiples=[],
        first_date="",
        last_date="",
    )
    result.trades = []
    result.execution_diagnostics["total_costs"] = 0.0
    evidence = _evidence(
        trade_count=0,
        ending_equity=1_000.0,
        gross_pnl=0.0,
        total_costs=0.0,
        net_pnl=0.0,
    )
    report = _validate(_input(result=result, evidence=evidence))
    assert report.target_state is RunState.INCONCLUSIVE
    assert [finding.code for finding in report.findings] == ["SUFF-001"]


def test_blocking_failure_takes_precedence_over_zero_trade_inconclusive():
    result = _result(
        n_trades=0,
        win_rate=2.0,
        total_pnl=0.0,
        winning_trades=0,
        losing_trades=0,
        r_sample_size=0,
        gross_pnl=0.0,
        total_costs=0.0,
        r_multiples=[],
    )
    result.trades = []
    result.execution_diagnostics["total_costs"] = 0.0
    evidence = _evidence(
        trade_count=0,
        ending_equity=1_000.0,
        gross_pnl=0.0,
        total_costs=0.0,
        net_pnl=0.0,
    )
    report = _validate(_input(result=result, evidence=evidence))
    assert report.target_state is RunState.VALIDATION_FAILED
