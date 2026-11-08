"""Warmup-aware segment execution (plan increment P4).

A walk-forward fold is only comparable to the fold beside it if both start
from the same state. Slicing the timeline and running each piece directly does
not achieve that: the first sessions of every fold run on cold indicators, so
they behave differently from the fold's own middle sessions *and* differently
from the same calendar dates inside a longer run. Averaging such folds
compares windows that were never measuring the same thing.

This module supplies the missing half of a split: the **warmup** sessions that
precede a graded segment. Their bars are loaded and fed through the engine so
indicators are primed, but they generate no orders, move no cash, and are
excluded from the equity curve and therefore from every metric denominator.

The invariant worth stating, because it is what the tests actually assert:

    Beyond the required lookback, additional warmup must not change a single
    graded trade.

That is falsifiable and it is the property folds need. It also fails loudly
against the tempting shortcut of "just let warmup trade and ignore its
results" -- a warmup trade moves cash, position sizing is a function of cash,
and every graded trade downstream would shift.

Warmup is a *context* requirement, not a leakage requirement, and must never
be conflated with purge (plan section 8). Reading sessions before a fold to
prime an indicator looks backwards in time, which is exactly what a causal
indicator is supposed to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from vibe.backtester.analysis.metrics import BacktestResult
from vibe.backtester.core.engine import BacktestEngine
from vibe.research_pipeline.contracts import SegmentRole, SessionSegment
from vibe.research_pipeline.splits.calendar import DEFAULT_CALENDAR, SessionCalendar

__all__ = [
    "SEGMENT_RUNNER_VERSION",
    "InsufficientWarmupError",
    "SegmentResult",
    "run_segment",
]

SEGMENT_RUNNER_VERSION = 1

# The engine compares tz-aware timestamps, so segment bounds must carry a
# zone. Exchange-local rather than UTC, because a session is identified by its
# exchange-local calendar date and converting would shift the boundary.
_EXCHANGE_TZ = ZoneInfo("America/New_York")


class InsufficientWarmupError(ValueError):
    """Raised when fewer warmup sessions exist than the caller required.

    Silently running short would be worse than failing: the segment would
    still produce numbers, they would be quietly less primed than a
    neighbouring fold's, and the resulting comparison would be wrong in a way
    nothing downstream could detect.
    """


@dataclass(frozen=True)
class SegmentResult:
    """A graded segment's result plus the warmup provenance behind it."""

    segment: SessionSegment
    result: BacktestResult
    warmup_sessions_requested: int
    warmup_sessions_supplied: int
    warmup_start: Optional[date]
    graded_start: date
    graded_end: date
    calendar_name: str = DEFAULT_CALENDAR
    runner_version: int = SEGMENT_RUNNER_VERSION

    @property
    def trades(self):
        return self.result.trades

    def assert_warmup_excluded(self) -> None:
        """Verify no trade escaped into the warmup region.

        Cheap enough to run on every segment, and it checks the one thing a
        caller cannot see from the metrics: that the suppression actually
        held. A trade dated before ``graded_start`` means warmup traded, which
        invalidates the whole comparison the segment exists to support.
        """
        stray = [
            t
            for t in self.result.trades
            if _trade_session(t) is not None and _trade_session(t) < self.graded_start
        ]
        if stray:
            raise AssertionError(
                f"{len(stray)} trade(s) occurred before graded_start "
                f"{self.graded_start}; warmup suppression failed and this "
                f"segment's metrics are not comparable to any other fold."
            )


def _trade_session(trade) -> Optional[date]:
    ts = getattr(trade, "entry_time", None)
    if ts is None:
        return None
    return ts.date() if isinstance(ts, datetime) else None


def _bound(day: date, end_of_day: bool) -> datetime:
    clock = time(23, 59, 59) if end_of_day else time(0, 0)
    return datetime.combine(day, clock, tzinfo=_EXCHANGE_TZ)


def run_segment(
    engine: BacktestEngine,
    symbol: str,
    segment: SessionSegment,
    warmup_sessions: int = 0,
    *,
    calendar: Optional[SessionCalendar] = None,
    require_full_warmup: bool = True,
) -> SegmentResult:
    """Execute one graded segment, primed on ``warmup_sessions`` prior sessions.

    Args:
        engine: A configured :class:`BacktestEngine`. Reused as-is so the
            segment inherits exactly the ruleset, capital, and execution
            realism the caller declared.
        symbol: Instrument to run.
        segment: The graded window. Only its session bounds are used, so a
            TRAIN, VALIDATION, TEST, or FINAL_OOS segment all run identically
            -- the role affects how the *result* may be used, not how it is
            produced.
        warmup_sessions: Sessions to prime on, counted in exchange sessions
            rather than calendar days so holidays cannot silently shorten it.
        require_full_warmup: Fail when history runs out rather than running
            under-primed.

    Returns:
        A :class:`SegmentResult` carrying the result and its warmup provenance.
    """
    if warmup_sessions < 0:
        raise ValueError(f"warmup_sessions must be non-negative, got {warmup_sessions}")

    cal = calendar or SessionCalendar(DEFAULT_CALENDAR)
    graded_start = segment.start_session
    graded_end = segment.end_session

    warmup: tuple[date, ...] = ()
    if warmup_sessions:
        warmup = cal.sessions_before(graded_start, warmup_sessions)
        if len(warmup) < warmup_sessions and require_full_warmup:
            raise InsufficientWarmupError(
                f"Segment starting {graded_start} requires {warmup_sessions} "
                f"warmup sessions but only {len(warmup)} exist on "
                f"{cal.name}. Running short would leave this fold less primed "
                f"than its neighbours while still producing numbers, so it is "
                f"refused. Move the segment later, reduce the requirement, or "
                f"pass require_full_warmup=False to accept the asymmetry."
            )

    load_start = warmup[0] if warmup else graded_start

    result = engine.run(
        symbol,
        start_date=_bound(load_start, end_of_day=False),
        end_date=_bound(graded_end, end_of_day=True),
        # Only sessions from here on are graded. Everything loaded before this
        # primes indicators and is then invisible to the result.
        graded_start=graded_start,
    )

    out = SegmentResult(
        segment=segment,
        result=result,
        warmup_sessions_requested=warmup_sessions,
        warmup_sessions_supplied=len(warmup),
        warmup_start=warmup[0] if warmup else None,
        graded_start=graded_start,
        graded_end=graded_end,
        calendar_name=cal.name,
    )
    out.assert_warmup_excluded()
    return out
