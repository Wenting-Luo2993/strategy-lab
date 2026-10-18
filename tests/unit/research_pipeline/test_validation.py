"""Synthetic fixtures for every P6 validation category."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from vibe.backtester.analysis.metrics import (
    BacktestResult,
    ConvexityMetrics,
)
from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.backtester.core.integrity import (
    EngineIntegrityCategory,
    EngineIntegrityReport,
    EngineIntegrityStatus,
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
    _binomial_tail_pvalue,
)


def _result(**metric_overrides) -> BacktestResult:
    trade_objects = metric_overrides.pop("trade_objects", 2)
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
    equity = PerformanceAnalyzer._calc_equity(
        list(zip(pd.bdate_range("2026-01-02", periods=10), [1000.0, 1100.0] * 5)),
        initial_capital=1000.0,
    )
    return BacktestResult(
        overall=overall,
        by_year={},
        equity=equity,
        trades=[object() for _ in range(trade_objects)],
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
            "max_funded_entry_leverage": 0.5,
            "total_costs": 10.0,
            "execution_model_version": 4.0,
            "accounting_failures": 0.0,
            "accounting_checks_applicable": 3.0,
        },
        engine_integrity_report=EngineIntegrityReport(
            status=EngineIntegrityStatus.PASS,
            engine_version=1,
            metric_calculation_version=2,
            checked_categories=tuple(EngineIntegrityCategory),
            findings=(),
        ),
    )


def _evidence(**overrides) -> RunEvidence:
    values = dict(
        trade_count=2,
        trade_ledger_sha256="1" * 64,
        equity_curve_sha256="2" * 64,
        equity_points=10,
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
    result.execution_diagnostics["max_funded_entry_leverage"] = 1.5
    report = _validate(_input(result=result), max_gross_leverage=1.0)
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "EXEC-002" in {finding.code for finding in report.findings}


def test_post_entry_price_drift_does_not_violate_funding_ceiling():
    result = _result()
    result.execution_diagnostics["max_gross_exposure_ratio"] = 1.5
    result.execution_diagnostics["max_funded_entry_leverage"] = 1.0
    report = _validate(_input(result=result), max_gross_leverage=1.0)
    assert "EXEC-002" not in {finding.code for finding in report.findings}


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


def _sample(wins: int, losses: int, **overrides) -> BacktestResult:
    """Build a result whose trade-level accounting matches its headline counts.

    The default fixture describes two trades, so simply raising ``n_trades``
    leaves the ledger, the r-multiples and the stop tallies contradicting it
    and trips the cross-check gates instead of the one under test.
    """
    n = wins + losses
    win_r = overrides.get("avg_win_r", 1.2)
    loss_r = overrides.get("avg_loss_r", -1.0)
    base = dict(
        n_trades=n,
        win_rate=wins / n,
        winning_trades=wins,
        losing_trades=losses,
        breakeven_trades=0,
        stop_wins=wins,
        stop_losses=losses,
        avg_win_r=win_r,
        avg_loss_r=loss_r,
        expectancy_r=(wins * win_r + losses * loss_r) / n,
        max_win_r=win_r if wins else 0.0,
        max_loss_r=loss_r if losses else 0.0,
        r_multiples=[win_r] * wins + [loss_r] * losses,
        r_sample_size=n,
        max_losing_streak=1 if losses else 0,
        trade_objects=n,
    )
    base.update(overrides)
    return _result(**base)


def _sample_input(wins: int, losses: int, *, scope, **overrides):
    """A ValidationInput whose evidence agrees with its metrics."""
    result = _sample(wins, losses, **overrides)
    return _input(
        result=result,
        evidence=_evidence(trade_count=wins + losses),
        scope=scope,
    )


def test_plausibility_only_applies_at_candidate_level():
    # 150 wins in 200 trades is a 75% rate that chance will not produce from
    # a 55% process, so this is implausible on the evidence rather than
    # merely above a threshold.
    sweep_input = _sample_input(
        150, 50, scope=ValidationScope.SWEEP_ROW, avg_win_r=1.2, avg_loss_r=-1.0
    )
    sweep_input.result.execution_diagnostics["ambiguous_exit_bars"] = 100.0
    candidate_input = _sample_input(
        150, 50, scope=ValidationScope.CANDIDATE, avg_win_r=1.2, avg_loss_r=-1.0
    )
    candidate_input.result.execution_diagnostics["ambiguous_exit_bars"] = 100.0

    sweep_row = _validate(sweep_input)
    candidate = _validate(candidate_input)

    assert sweep_row.target_state is RunState.COMPLETED
    assert candidate.target_state is RunState.REVIEW_REQUIRED
    assert {"PLAUS-001", "PLAUS-002"} <= {
        finding.code for finding in candidate.findings
    }


def test_a_lucky_streak_is_not_mistaken_for_an_implausible_edge():
    """Nine wins in fourteen trades is what a coin does regularly.

    This is the defect that blocked every nested walk-forward from reaching a
    TEST window: PLAUS-001 compared a ratio to a threshold with no regard for
    how much evidence stood behind it, so a short validation segment tripped
    it as a matter of course and the fold went inconclusive. A gate that
    fires on noise does not protect anyone -- it just teaches people to
    ignore it.
    """
    report = _validate(
        _sample_input(
            9, 5, scope=ValidationScope.CANDIDATE, avg_win_r=1.2, avg_loss_r=-1.0
        )
    )

    assert "PLAUS-001" not in {finding.code for finding in report.findings}


def test_the_same_win_rate_is_implausible_once_enough_trades_back_it():
    """The threshold does not move; only the weight of evidence does."""
    small = _sample_input(
        9, 5, scope=ValidationScope.CANDIDATE, avg_win_r=1.2, avg_loss_r=-1.0
    )
    large = _sample_input(
        320, 180, scope=ValidationScope.CANDIDATE, avg_win_r=1.2, avg_loss_r=-1.0
    )

    def codes(value):
        return {finding.code for finding in _validate(value).findings}

    assert round(small.result.overall.win_rate, 2) == round(
        large.result.overall.win_rate, 2
    )
    assert "PLAUS-001" not in codes(small)
    assert "PLAUS-001" in codes(large), (
        "320 wins in 500 trades at a 55% null is a one-in-forty-thousand "
        "result; if the gate stays quiet here it is measuring nothing"
    )


def test_payoff_asymmetry_still_excuses_a_high_win_rate():
    """Sample size was added as a second condition, not as a replacement."""
    report = _validate(
        _sample_input(
            320, 180, scope=ValidationScope.CANDIDATE, avg_win_r=0.4, avg_loss_r=-1.0
        )
    )

    assert "PLAUS-001" not in {finding.code for finding in report.findings}


@pytest.mark.parametrize(
    "wins, n, p",
    [(0, 0, 0.55), (0, 10, 0.55), (5, 10, 0.0), (5, 10, 1.0)],
)
def test_an_undefined_plausibility_test_never_convicts(wins, n, p):
    """A degenerate configuration is a bug report, not damning evidence."""
    assert _binomial_tail_pvalue(wins=wins, n=n, p=p) == 1.0


def test_more_wins_than_trades_is_clamped_rather_than_crashing():
    assert _binomial_tail_pvalue(wins=20, n=10, p=0.55) == pytest.approx(
        _binomial_tail_pvalue(wins=10, n=10, p=0.55)
    )


def test_a_long_run_does_not_underflow_into_looking_impossible():
    """Computed in log space; (1-p)**n would have been 0.0 long before here.

    Were this to underflow, every sufficiently long run would report a
    p-value of zero and be flagged as implausible -- the failure would look
    like a strategy finding rather than a numerical one.
    """
    at_the_null = _binomial_tail_pvalue(wins=1100, n=2000, p=0.55)

    assert 0.4 < at_the_null < 0.6, (
        f"a result sitting exactly on the null should be unremarkable, got "
        f"{at_the_null}"
    )


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


def _return_input(returns, *, scope=ValidationScope.CANDIDATE, intraday=False):
    values = [1000.0]
    for change in returns:
        values.append(values[-1] * (1 + change))
    sessions = pd.bdate_range("2026-01-02", periods=len(values), tz="America/New_York")
    observations = []
    for session, close in zip(sessions, values):
        if intraday:
            observations.append((session + pd.Timedelta(hours=10), close * 0.99))
        observations.append((session + pd.Timedelta(hours=16), close))
    result = _result()
    result.equity = PerformanceAnalyzer._calc_equity(observations, initial_capital=1000.0)
    return _input(
        result=result,
        evidence=_evidence(equity_points=len(observations)),
        scope=scope,
        data=DataIntegritySummary(
            expected_sessions=len(values),
            observed_sessions=len(values),
            bar_count=len(observations),
        ),
    )


@pytest.mark.parametrize("scope", list(ValidationScope))
@pytest.mark.parametrize("metric", ["expectancy_r", "sharpe_ratio"])
def test_incorrect_metric_arithmetic_blocks_every_scope(scope, metric):
    value = _input(scope=scope)
    target = value.result.overall if metric == "expectancy_r" else value.result.equity
    setattr(target, metric, getattr(target, metric) + 10)
    report = _validate(value)
    assert report.target_state is RunState.VALIDATION_FAILED
    code = "CROSS-006" if metric == "expectancy_r" else "CROSS-007"
    finding = next(finding for finding in report.findings if finding.code == code)
    assert finding.metric_key == metric
    assert finding.expected and finding.context


@pytest.mark.parametrize("sample", [[], [1.0], [1.0, float("nan")], [1.0, float("inf")]])
def test_unusable_r_samples_cannot_hide_behind_finite_headlines(sample):
    report = _validate(_input(result=_result(r_multiples=sample)))
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "CROSS-005" in {finding.code for finding in report.findings}


@pytest.mark.parametrize("scale", [252**0.5, 78**0.5, -1.0])
def test_wrong_sharpe_annualization_or_sign_is_not_just_a_review_warning(scale):
    value = _return_input([0.02, -0.01, 0.015, -0.005] * 6)
    value.result.equity.sharpe_ratio *= scale
    report = _validate(value)
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "CROSS-007" in {finding.code for finding in report.findings}


def test_daily_sharpe_audit_ignores_intraday_frequency_and_calendar_gaps():
    daily = _return_input([0.01, -0.01] * 35)
    intraday = _return_input([0.01, -0.01] * 35, intraday=True)
    assert daily.result.equity.sharpe_ratio == pytest.approx(intraday.result.equity.sharpe_ratio)
    assert _validate(daily).target_state is RunState.COMPLETED
    assert _validate(intraday).target_state is RunState.COMPLETED


@pytest.mark.parametrize("mutation", ["duplicate", "unordered", "nonpositive", "nan", "session_count"])
def test_invalid_equity_evidence_is_a_blocking_diagnosis_not_an_exception(mutation):
    value = _return_input([0.01, -0.01] * 10)
    equity = value.result.equity
    if mutation == "duplicate":
        equity.equity_curve.index = pd.DatetimeIndex([equity.equity_curve.index[0]] * len(equity.equity_curve))
    elif mutation == "unordered":
        equity.equity_curve = equity.equity_curve.iloc[::-1]
    elif mutation == "nonpositive":
        equity.equity_curve.iloc[3] = 0.0
    elif mutation == "nan":
        equity.equity_curve.iloc[3] = float("nan")
    else:
        equity.n_sessions += 1
    report = _validate(value)
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "CROSS-008" in {finding.code for finding in report.findings}


@pytest.mark.parametrize("expectancy", [24.5, -24.5])
def test_consistent_but_extreme_expectancy_requires_review_not_arithmetic_failure(expectancy):
    result = _sample(
        1, 1,
        avg_win_r=50.0 if expectancy > 0 else 1.0,
        avg_loss_r=-1.0 if expectancy > 0 else -50.0,
    )
    assert result.overall.expectancy_r == expectancy
    report = _validate(_input(
        result=result,
        scope=ValidationScope.CANDIDATE,
    ))
    assert report.target_state is RunState.REVIEW_REQUIRED
    assert {finding.code for finding in report.findings} == {"PLAUS-004"}


def test_expectancy_envelope_is_explicit_and_configurable():
    value = _input(
        result=_sample(1, 1, avg_win_r=10.0),
        scope=ValidationScope.CANDIDATE,
    )
    assert _validate(value).target_state is RunState.COMPLETED
    report = _validate(value, max_expectancy_r=1.0)
    assert report.target_state is RunState.REVIEW_REQUIRED
    assert next(f for f in report.findings if f.code == "PLAUS-004").observed == 4.5


@pytest.mark.parametrize("sign", [1, -1])
def test_extreme_daily_sharpe_is_reviewed_in_both_directions(sign):
    report = _validate(_return_input([sign * 0.01, sign * 0.011] * 15))
    assert report.target_state is RunState.REVIEW_REQUIRED
    assert "PLAUS-005" in {finding.code for finding in report.findings}
    assert not any(f.code.startswith("CROSS") for f in report.findings)


def test_short_lucky_window_does_not_trip_long_sample_sharpe_checks():
    value = _return_input([0.01, 0.011] * 5)
    assert abs(value.result.equity.sharpe_ratio) > 5
    assert _validate(value).target_state is RunState.COMPLETED
    assert _validate(value, min_sharpe_returns=10).target_state is RunState.REVIEW_REQUIRED


def test_default_sharpe_minimum_is_exactly_twenty_returns():
    assert _validate(_return_input([0.01, 0.011] * 9 + [0.01])).target_state is RunState.COMPLETED
    assert _validate(_return_input([0.01, 0.011] * 10)).target_state is RunState.REVIEW_REQUIRED


def test_using_population_instead_of_sample_deviation_is_a_calculation_failure():
    value = _return_input([0.02, -0.01, 0.015, -0.005] * 6)
    value.result.equity.sharpe_ratio *= (24 / 23) ** 0.5
    report = _validate(value)
    assert report.target_state is RunState.VALIDATION_FAILED
    assert "CROSS-007" in {finding.code for finding in report.findings}


@pytest.mark.parametrize("scope", [ValidationScope.RUN, ValidationScope.SWEEP_ROW])
def test_range_reviews_do_not_turn_a_sweep_into_manual_approval_per_row(scope):
    report = _validate(_return_input([0.01, 0.011] * 15, scope=scope))
    assert report.target_state is RunState.COMPLETED
    assert not any(f.code.startswith("PLAUS") for f in report.findings)


@pytest.mark.parametrize("outlier", [0.5, -0.5])
def test_single_session_concentration_explains_sharpe_sensitivity(outlier):
    report = _validate(_return_input([0.001, -0.001] * 15 + [outlier]))
    finding = next(f for f in report.findings if f.code == "PLAUS-006")
    assert report.target_state is RunState.REVIEW_REQUIRED
    assert finding.observed > 0.9
    assert finding.context["return_count"] == 31
    assert finding.context["session_return"] == pytest.approx(outlier)
    assert finding.context["sharpe_without_largest_session"] == pytest.approx(0.0, abs=1e-10)


def test_near_constant_nonzero_returns_warn_but_a_flat_curve_does_not():
    report = _validate(_return_input([0.01] * 30))
    assert "PLAUS-007" in {f.code for f in report.findings}
    assert _validate(_return_input([0.0] * 30)).target_state is RunState.COMPLETED


@pytest.mark.parametrize("overrides", [
    {"min_expectancy_r": 5, "max_expectancy_r": 1},
    {"max_expectancy_r": float("inf")},
    {"max_abs_sharpe": 0},
    {"min_sharpe_returns": 1},
    {"max_single_session_return_share": 1.01},
    {"min_session_return_std": 0},
])
def test_invalid_diagnostic_profiles_are_rejected(overrides):
    with pytest.raises(ValueError):
        ValidationProfile(**overrides)


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
