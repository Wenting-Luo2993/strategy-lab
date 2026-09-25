"""Tests for the leakage harness (P5), including fixtures F1 and F2.

Every check here is exercised twice: once against a feature that is genuinely
causal, and once against one that is not. A leakage check that has only ever
been seen to pass is indistinguishable from a check that cannot fail, and the
second kind is worse than none because it converts an open question into a
false assurance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vibe.research_pipeline.features.leakage import (
    LeakageReport,
    LeakageViolation,
    audit_feature_availability,
    check_future_perturbation,
    check_orb_boundary,
    check_prefix_invariance,
    check_split_contamination,
    check_truncation_equivalence,
    run_leakage_suite,
)
from vibe.research_pipeline.contracts import FeatureDeclaration, FeatureKind
from vibe.research_pipeline.features.registry import FEATURE_REGISTRY

#: See the identically-named constant in ``test_registry.py``. Every shipped
#: feature is causal now that the daily-resample leak is fixed, so the audit's
#: rejecting branch needs an injected diagnostic to stay reachable.
_REGISTRY_WITH_LEAK = dict(FEATURE_REGISTRY) | {
    "leaky_probe": FeatureDeclaration(
        name="leaky_probe",
        kind=FeatureKind.DIAGNOSTIC,
        lookback_bars=1,
        lookahead_bars=77,
        description=(
            "Synthetic. Reproduces the construction that caused the original "
            "bug: a whole-session daily aggregate forward-filled onto intraday "
            "bars with no lag."
        ),
    )
}


@pytest.fixture
def bars() -> pd.DataFrame:
    """Deterministic intraday bars, three 78-bar sessions of 5-minute data."""
    sessions = []
    rng = np.random.default_rng(7)
    for day in range(3):
        idx = pd.date_range(
            f"2024-01-0{day + 2} 09:30", periods=78, freq="5min", tz="America/New_York"
        )
        close = 100 + np.cumsum(rng.normal(0, 0.15, 78))
        sessions.append(
            pd.DataFrame(
                {
                    "open": close,
                    "high": close + 0.4,
                    "low": close - 0.4,
                    "close": close,
                    "volume": rng.uniform(900, 1100, 78),
                },
                index=idx,
            )
        )
    return pd.concat(sessions)


def causal_compute(df: pd.DataFrame) -> pd.DataFrame:
    """A trailing mean. Depends only on the current bar and earlier."""
    return pd.DataFrame(
        {"trailing_mean": df["close"].rolling(10, min_periods=10).mean()},
        index=df.index,
    )


def leaky_compute(df: pd.DataFrame) -> pd.DataFrame:
    """F1: a feature defined as ``close.shift(-1)`` but presented as causal.

    This is the canonical leak: the column name says nothing about the shift,
    and the value at bar ``t`` is simply the close of bar ``t+1``.
    """
    return pd.DataFrame({"next_close": df["close"].shift(-1)}, index=df.index)


def session_aggregate_compute(df: pd.DataFrame) -> pd.DataFrame:
    """The real-world leak: whole-session aggregate forward-filled intraday.

    This reproduces the construction used by ``adx_14`` / ``slope_20d`` /
    ``slope_50d`` in ``FeatureEngine``: resample to daily, compute, then
    ``reindex(..., method="ffill")`` with no shift.
    """
    daily = df["close"].resample("D").last().dropna()
    return pd.DataFrame(
        {"session_close": daily.reindex(df.index, method="ffill")}, index=df.index
    )


# A bar in the middle of the second session: daily features are primed, and
# there are bars both before and after it within the same session.
MID_SESSION = 78 + 40


class TestTruncationEquivalence:
    """Check 3: a truncated run must match the prefix of a full run."""

    def test_causal_feature_passes(self, bars):
        report = check_truncation_equivalence(bars, causal_compute, cut=MID_SESSION)
        assert report.passed, str(report)

    def test_f1_next_close_is_caught(self, bars):
        report = check_truncation_equivalence(bars, leaky_compute, cut=MID_SESSION)
        assert not report.passed
        assert report.features_failing() == ("next_close",)

    def test_session_aggregate_leak_is_caught(self, bars):
        report = check_truncation_equivalence(
            bars, session_aggregate_compute, cut=MID_SESSION
        )
        assert not report.passed, (
            "A whole-session aggregate forward-filled onto intraday bars must "
            "be caught; this is the exact construction that contaminates "
            "adx_14 and slope_20d."
        )

    def test_failure_names_the_timestamp_and_both_values(self, bars):
        report = check_truncation_equivalence(bars, leaky_compute, cut=MID_SESSION)
        finding = report.failures[0]
        assert finding.first_divergence == bars.index[MID_SESSION]
        assert finding.full_value is not None
        assert finding.restricted_value is None or finding.full_value != finding.restricted_value

    def test_cut_outside_frame_is_rejected(self, bars):
        with pytest.raises(ValueError, match="outside the frame"):
            check_truncation_equivalence(bars, causal_compute, cut=len(bars) + 5)


class TestPrefixInvariance:
    """Check 1: altering later bars cannot move earlier values."""

    def test_causal_feature_passes(self, bars):
        report = check_prefix_invariance(bars, causal_compute, cut=MID_SESSION)
        assert report.passed, str(report)

    def test_f1_next_close_is_caught(self, bars):
        report = check_prefix_invariance(bars, leaky_compute, cut=MID_SESSION)
        assert not report.passed

    def test_session_aggregate_leak_is_caught(self, bars):
        # This is the case truncation alone could miss: the later bars still
        # exist, they are merely different.
        report = check_prefix_invariance(
            bars, session_aggregate_compute, cut=MID_SESSION
        )
        assert not report.passed

    def test_cut_with_no_future_bars_is_vacuous_and_rejected(self, bars):
        with pytest.raises(ValueError, match="vacuous"):
            check_prefix_invariance(bars, causal_compute, cut=len(bars) - 1)


class TestFuturePerturbation:
    """Check 2: extreme future values must not reach prior decisions."""

    def test_causal_feature_passes(self, bars):
        report = check_future_perturbation(bars, causal_compute, cut=MID_SESSION)
        assert report.passed, str(report)

    def test_f1_next_close_is_caught(self, bars):
        report = check_future_perturbation(bars, leaky_compute, cut=MID_SESSION)
        assert not report.passed

    def test_session_aggregate_leak_is_caught(self, bars):
        report = check_future_perturbation(
            bars, session_aggregate_compute, cut=MID_SESSION
        )
        assert not report.passed

    def test_cut_with_no_future_bars_is_vacuous_and_rejected(self, bars):
        with pytest.raises(ValueError, match="vacuous"):
            check_future_perturbation(bars, causal_compute, cut=len(bars) - 1)


class TestFeatureAvailabilityAudit:
    """Check 4: a declaration audit, independent of any computation."""

    def test_causal_features_pass(self):
        report = audit_feature_availability(["atr_14", "gap_pct"])
        assert report.passed

    def test_diagnostic_feature_fails(self):
        """Injected, because no shipped feature is diagnostic any more.

        Pointing this at a real feature name would have silently turned into a
        no-op when adx_14 was fixed and promoted to causal.
        """
        report = audit_feature_availability(
            ["atr_14", "leaky_probe"], registry=_REGISTRY_WITH_LEAK
        )
        assert not report.passed
        assert report.features_failing() == ("leaky_probe",)

    def test_previously_leaky_features_now_pass(self):
        # Regression guard: these three were the original convictions.
        report = audit_feature_availability(["adx_14", "slope_20d", "slope_50d"])
        assert report.passed

    def test_undeclared_feature_fails(self):
        report = audit_feature_availability(["mystery_alpha"])
        assert not report.passed

    def test_failure_explains_why(self):
        report = audit_feature_availability(
            ["leaky_probe"], registry=_REGISTRY_WITH_LEAK
        )
        assert "whole-session daily aggregate" in report.failures[0].detail


class TestORBBoundary:
    """Check 5: the breakout bar must not be part of its own opening range."""

    @pytest.fixture
    def session(self):
        idx = pd.date_range(
            "2024-01-02 09:30", periods=12, freq="5min", tz="America/New_York"
        )
        highs = [101, 102, 103, 109, 105, 106, 107, 108, 110, 111, 112, 113]
        lows = [99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88]
        return pd.DataFrame(
            {
                "open": [100.0] * 12,
                "high": [float(h) for h in highs],
                "low": [float(low) for low in lows],
                "close": [100.0] * 12,
                "volume": [1000.0] * 12,
            },
            index=idx,
        )

    def test_correct_boundary_passes(self, session):
        # Opening range = first 3 bars (09:30, 09:35, 09:40); window ends 09:45.
        or_end = session.index[3]
        window = session.iloc[:3]
        report = check_orb_boundary(
            session,
            or_high=float(window["high"].max()),
            or_low=float(window["low"].min()),
            session_open=session.index[0],
            or_end=or_end,
            breakout_time=session.index[3],
        )
        assert report.passed, str(report)

    def test_breakout_bar_folded_into_range_is_caught(self, session):
        # The 09:45 bar has high=109, far above the true range high of 103.
        # Including it is the self-fulfilling failure the check exists for.
        or_end = session.index[3]
        window = session.iloc[:4]  # one bar too many
        report = check_orb_boundary(
            session,
            or_high=float(window["high"].max()),
            or_low=float(window["low"].min()),
            session_open=session.index[0],
            or_end=or_end,
            breakout_time=session.index[3],
        )
        assert not report.passed
        assert "or_high" in report.features_failing()

    def test_breakout_inside_the_range_window_is_caught(self, session):
        or_end = session.index[3]
        window = session.iloc[:3]
        report = check_orb_boundary(
            session,
            or_high=float(window["high"].max()),
            or_low=float(window["low"].min()),
            session_open=session.index[0],
            or_end=or_end,
            breakout_time=session.index[1],  # inside the range window
        )
        assert not report.passed
        assert "breakout_time" in report.features_failing()

    def test_empty_range_window_is_inconclusive_not_silently_ok(self, session):
        report = check_orb_boundary(
            session,
            or_high=100.0,
            or_low=99.0,
            session_open=session.index[0],
            or_end=session.index[0],  # zero-width window
            breakout_time=session.index[3],
        )
        assert not report.passed
        assert "or_window" in report.features_failing()


class TestSplitContamination:
    """Check 6: name the overlapping session, do not merely report overlap."""

    def test_disjoint_splits_pass(self):
        report = check_split_contamination(
            train_sessions=["2024-01-02", "2024-01-03"],
            test_sessions=["2024-01-04", "2024-01-05"],
        )
        assert report.passed

    def test_overlapping_train_and_test_is_caught(self):
        report = check_split_contamination(
            train_sessions=["2024-01-02", "2024-01-03", "2024-01-04"],
            test_sessions=["2024-01-04", "2024-01-05"],
        )
        assert not report.passed

    def test_failure_names_the_specific_session(self):
        report = check_split_contamination(
            train_sessions=["2024-01-02", "2024-01-04"],
            test_sessions=["2024-01-04"],
        )
        assert "2024-01-04" in report.failures[0].detail

    def test_selector_seeing_test_sessions_is_caught(self):
        report = check_split_contamination(
            train_sessions=["2024-01-02"],
            test_sessions=["2024-01-04"],
            selection_sessions=["2024-01-02", "2024-01-04"],
        )
        assert not report.passed
        assert "selection_vs_test" in report.features_failing()

    def test_clean_selector_passes(self):
        report = check_split_contamination(
            train_sessions=["2024-01-02"],
            test_sessions=["2024-01-04"],
            selection_sessions=["2024-01-02"],
        )
        assert report.passed


class TestReport:
    def test_raise_if_failed_is_silent_when_clean(self, bars):
        check_truncation_equivalence(
            bars, causal_compute, cut=MID_SESSION
        ).raise_if_failed()

    def test_raise_if_failed_names_every_failure(self, bars):
        report = check_truncation_equivalence(bars, leaky_compute, cut=MID_SESSION)
        with pytest.raises(LeakageViolation, match="next_close"):
            report.raise_if_failed()

    def test_empty_report_passes(self):
        assert LeakageReport().passed


class TestSuite:
    def test_suite_passes_on_a_causal_feature(self, bars):
        report = run_leakage_suite(
            bars, causal_compute, cut=MID_SESSION, features=["atr_14"]
        )
        # atr_14 is not produced by causal_compute, so only the declaration
        # audit contributes a finding - and it should pass.
        assert report.passed, str(report)

    def test_suite_catches_f1_on_all_three_measured_checks(self, bars):
        report = run_leakage_suite(
            bars, leaky_compute, cut=MID_SESSION, features=["next_close"]
        )
        failed_checks = {f.check for f in report.failures}
        assert {
            "truncation_equivalence",
            "prefix_invariance",
            "future_perturbation",
        } <= failed_checks
        # And the audit refuses it too, because it was never declared.
        assert "feature_availability" in failed_checks
