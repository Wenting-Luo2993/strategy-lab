"""F3 and the warmup-aware segment execution suite (plan increment P4).

The property under test is the one folds actually need:

    Beyond the required lookback, additional warmup must not change a single
    graded trade -- and warmup sessions must produce no trades and appear in
    no metric denominator.

Establishing that honestly required finding a configuration where warmup
*does* something. Under the default (legacy) execution path, ORB's trades are
identical with and without warmup, because nothing on that path consumes ATR
or ADV. A suite written only against that path would pass while asserting
nothing. Under realistic execution the picture changes: ADV is NaN across the
whole first session without warmup, so the impact model prices fills
differently. ``TestWarmupActuallyMatters`` pins that difference, and it is
what makes the convergence tests meaningful rather than decorative.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution.config import ExecutionConfig
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.research_pipeline.contracts import SegmentRole, SessionSegment
from vibe.research_pipeline.segment_runner import (
    InsufficientWarmupError,
    SegmentResult,
    run_segment,
)
from vibe.research_pipeline.splits.calendar import SessionCalendar

NY = ZoneInfo("America/New_York")
SYMBOL = "QQQ"

# Deliberately short so the suite stays runnable. The invariants are structural
# and do not need a long window to expose a violation.
GRADED_START = date(2022, 3, 1)
GRADED_END = date(2022, 4, 8)


@pytest.fixture(scope="module")
def calendar() -> SessionCalendar:
    return SessionCalendar("XNYS")


@pytest.fixture(scope="module")
def ruleset():
    return RuleSetLoader.from_name("orb_production")


@pytest.fixture(scope="module")
def graded_sessions(calendar) -> tuple[date, ...]:
    return calendar.sessions_between(GRADED_START, GRADED_END)


@pytest.fixture(scope="module")
def segment(graded_sessions) -> SessionSegment:
    return SessionSegment(
        role=SegmentRole.TEST,
        start_session=graded_sessions[0],
        end_session=graded_sessions[-1],
        session_count=len(graded_sessions),
    )


def _signature(res: SegmentResult):
    """Trade identity strong enough that any behavioural drift shows up."""
    return [
        (t.entry_time.isoformat(), round(t.entry_price, 6), t.quantity, round(t.pnl, 6))
        for t in res.result.trades
    ]


def _run(ruleset, segment, warmup, *, realistic=False, calendar=None):
    engine = BacktestEngine(
        ruleset=ruleset,
        execution_config=(
            ExecutionConfig.realistic(participation_rate=0.0005) if realistic else None
        ),
    )
    return run_segment(engine, SYMBOL, segment, warmup_sessions=warmup, calendar=calendar)


@pytest.fixture(scope="module")
def legacy_runs(ruleset, segment, calendar):
    return {w: _run(ruleset, segment, w, calendar=calendar) for w in (0, 20, 40)}


@pytest.fixture(scope="module")
def realistic_runs(ruleset, segment, calendar):
    return {
        w: _run(ruleset, segment, w, realistic=True, calendar=calendar)
        for w in (0, 20, 40)
    }


class TestF3WarmupProducesNoTradesAndNoDenominator:
    """F3 proper."""

    def test_no_trade_precedes_the_graded_window(self, legacy_runs, segment):
        for warmup, res in legacy_runs.items():
            earliest = min(t.entry_time.date() for t in res.result.trades)
            assert earliest >= segment.start_session, (
                f"warmup={warmup} produced a trade on {earliest}, before the "
                f"graded window opened on {segment.start_session}."
            )

    def test_warmup_sessions_are_absent_from_every_denominator(
        self, legacy_runs, graded_sessions
    ):
        # n_sessions is derived by resampling the equity curve, so if warmup
        # were present here it would silently inflate every session-based
        # metric, Sharpe most of all.
        for warmup, res in legacy_runs.items():
            assert res.result.equity.n_sessions == len(graded_sessions), (
                f"warmup={warmup} reported {res.result.equity.n_sessions} "
                f"sessions against {len(graded_sessions)} graded ones."
            )

    def test_warmup_was_actually_loaded(self, legacy_runs):
        # The complement of the test above: sessions must be excluded from the
        # metrics *and* have genuinely been fed to the engine. Asserting only
        # the exclusion would pass against an implementation that never loaded
        # warmup at all, which is precisely the bug P4 exists to fix.
        assert legacy_runs[0].result.execution_diagnostics[
            "warmup_sessions_excluded"
        ] == 0.0
        assert legacy_runs[20].result.execution_diagnostics[
            "warmup_sessions_excluded"
        ] == 20.0
        assert legacy_runs[40].result.execution_diagnostics[
            "warmup_sessions_excluded"
        ] == 40.0

    def test_reported_window_is_the_graded_window(self, legacy_runs, segment):
        # Not the warmup load boundary. A segment that misreported its own span
        # would throw off any fold-length comparison built on it.
        for res in legacy_runs.values():
            assert str(res.result.start_date).startswith(
                segment.start_session.isoformat()
            ), (
                f"Reported start {res.result.start_date!r} is not the graded "
                f"start {segment.start_session}."
            )


class TestWarmupActuallyMatters:
    """Without this class the convergence tests below would be vacuous."""

    def test_zero_warmup_changes_fills_under_realistic_execution(
        self, realistic_runs
    ):
        # ADV is NaN across the entire first session with no warmup (the
        # rolling window is 20 sessions), so the impact model prices fills
        # differently. This is the observable proof that priming is real.
        assert _signature(realistic_runs[0]) != _signature(realistic_runs[20])

    def test_legacy_path_is_indifferent_to_warmup(self, legacy_runs):
        # Recorded rather than hidden: on the default execution path nothing
        # consumes ATR or ADV, so ORB's warmup requirement is genuinely 0
        # there. The required warmup is a property of what the execution
        # config consumes, not of the strategy alone -- and a suite that only
        # exercised this path would assert nothing.
        assert _signature(legacy_runs[0]) == _signature(legacy_runs[20])


class TestWarmupConvergence:
    """The invariant that makes folds comparable."""

    def test_extra_warmup_changes_nothing_under_realistic_execution(
        self, realistic_runs
    ):
        assert _signature(realistic_runs[20]) == _signature(realistic_runs[40]), (
            "Warmup beyond the required lookback changed graded trades. Either "
            "warmup is leaking into the graded window, or the lookback is "
            "longer than assumed; either way folds are not comparable."
        )

    def test_extra_warmup_changes_nothing_on_the_legacy_path(self, legacy_runs):
        assert _signature(legacy_runs[20]) == _signature(legacy_runs[40])

    def test_capital_is_identical_at_the_graded_open(self, realistic_runs):
        # The sharpest available check for a warmup trade. Suppressing *orders*
        # is not sufficient on its own: a warmup fill would move cash, position
        # sizing is a function of cash, and every graded trade downstream would
        # shift. Position size is therefore a direct probe of the cash balance
        # at the graded open -- and note it must hold even between warmup=0 and
        # warmup=20, whose fill *prices* legitimately differ.
        sizes = {
            w: r.result.trades[0].quantity for w, r in realistic_runs.items()
        }
        assert len(set(sizes.values())) == 1, (
            f"First graded trade sized differently per warmup length: {sizes}. "
            f"Warmup moved cash, so these segments are not comparable."
        )


class TestWarmupIsCountedInSessions:
    def test_warmup_start_is_a_session(self, legacy_runs, calendar):
        for warmup, res in legacy_runs.items():
            if res.warmup_start is not None:
                assert calendar.is_session(res.warmup_start)

    def test_warmup_spans_more_calendar_days_than_sessions(self, legacy_runs, segment):
        # 20 sessions is about 28 calendar days. Counting in days would quietly
        # under-prime every fold that straddled a holiday.
        res = legacy_runs[20]
        span = (segment.start_session - res.warmup_start).days
        assert span > 20
        assert res.warmup_sessions_supplied == 20

    def test_supplied_matches_requested(self, legacy_runs):
        for warmup, res in legacy_runs.items():
            assert res.warmup_sessions_requested == warmup
            assert res.warmup_sessions_supplied == warmup


class TestInsufficientWarmup:
    def test_refuses_to_run_under_primed(self, ruleset, calendar):
        # The data begins 2018-05-01. A segment at the very start of the
        # calendar cannot be given deep warmup.
        early = calendar.sessions_between(date(1970, 1, 2), date(1970, 2, 27))
        seg = SessionSegment(
            role=SegmentRole.TRAIN,
            start_session=early[0],
            end_session=early[-1],
            session_count=len(early),
        )
        with pytest.raises(InsufficientWarmupError, match="warmup sessions but only"):
            _run(ruleset, seg, 5000, calendar=calendar)

    def test_negative_warmup_rejected(self, ruleset, segment, calendar):
        with pytest.raises(ValueError, match="non-negative"):
            _run(ruleset, segment, -1, calendar=calendar)


class TestStrayTradeDetection:
    def test_assert_warmup_excluded_catches_a_leaked_trade(self, legacy_runs):
        # Mutation: if suppression ever regressed, a graded result would carry
        # a trade dated before the window. Prove the guard would notice.
        res = legacy_runs[20]
        leaked = SegmentResult(
            segment=res.segment,
            result=res.result,
            warmup_sessions_requested=res.warmup_sessions_requested,
            warmup_sessions_supplied=res.warmup_sessions_supplied,
            warmup_start=res.warmup_start,
            graded_start=date(2022, 12, 1),  # pretend the window opened later
            graded_end=res.graded_end,
        )
        with pytest.raises(AssertionError, match="warmup suppression failed"):
            leaked.assert_warmup_excluded()
