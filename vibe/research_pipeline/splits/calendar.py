"""Exchange session calendar over ``pandas_market_calendars``.

A *session* is one regular-hours exchange trading day, identified by its
exchange-local calendar date (``datetime.date``). Weekends and exchange
holidays are not sessions. A half day (early close) is still exactly one
session; the early-close flag is exposed so callers can decide what to do with
it, but it never changes the session count.

Splits elsewhere in the pipeline are enumerated in these sessions rather than
calendar days, because calendar arithmetic (``months * 30``) drifts against
holidays and half-days and makes nominally-equal folds contain different
numbers of trading opportunities.

The wrapper is pure and deterministic: it performs no network I/O, and building
a :class:`SessionCalendar` at import time is safe.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

import pandas_market_calendars as mcal
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_CALENDAR",
    "SessionCalendar",
    "SessionReconciliation",
    "reconcile_sessions",
]

DEFAULT_CALENDAR = "XNYS"

# Absolute ceiling on how far the sliding-window helpers will scan before
# giving up, so a bad argument can never spin forever.
_MAX_SCAN_DAYS = 4000


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Expected date/datetime/ISO string, got {type(value).__name__}")


class SessionReconciliation(BaseModel):
    """Structured discrepancy report between calendar and loaded bar data.

    A silently missing session shifts every downstream fold boundary, so the
    result is returned rather than logged. ``missing_from_data`` are calendar
    sessions with no bars (data holes); ``unexpected_in_data`` are dates present
    in the data that the exchange calendar does not consider sessions (bad
    resampling, wrong calendar, or duplicated rows on a non-session date).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    calendar_name: str = Field(..., min_length=1)
    missing_from_data: tuple[date, ...] = ()
    unexpected_in_data: tuple[date, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.missing_from_data and not self.unexpected_in_data


def reconcile_sessions(
    expected: Iterable[date],
    actual: Iterable[date],
    *,
    calendar_name: str = DEFAULT_CALENDAR,
) -> SessionReconciliation:
    """Compare an expected session list with dates present in bar data.

    Discrepancies are reported in both directions so neither a data hole nor a
    stray extra date can pass unnoticed.
    """
    expected_set = {_as_date(d) for d in expected}
    actual_set = {_as_date(d) for d in actual}
    return SessionReconciliation(
        calendar_name=calendar_name,
        missing_from_data=tuple(sorted(expected_set - actual_set)),
        unexpected_in_data=tuple(sorted(actual_set - expected_set)),
    )


class SessionCalendar:
    """Deterministic wrapper enumerating regular-hours exchange sessions."""

    def __init__(self, name: str = DEFAULT_CALENDAR) -> None:
        self._name = name
        self._cal = mcal.get_calendar(name)

    @property
    def name(self) -> str:
        return self._name

    def _schedule_dates(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        schedule = self._cal.schedule(
            start_date=start.isoformat(), end_date=end.isoformat()
        )
        return [ts.date() for ts in schedule.index]

    def sessions_between(
        self, start: date | datetime | str, end: date | datetime | str
    ) -> tuple[date, ...]:
        """Return every session in ``[start, end]`` inclusive, ascending."""
        start_d = _as_date(start)
        end_d = _as_date(end)
        if end_d < start_d:
            raise ValueError(f"end ({end_d}) is before start ({start_d})")
        return tuple(self._schedule_dates(start_d, end_d))

    def session_count(
        self, start: date | datetime | str, end: date | datetime | str
    ) -> int:
        """Number of sessions in ``[start, end]`` inclusive."""
        return len(self.sessions_between(start, end))

    def is_session(self, d: date | datetime | str) -> bool:
        """True if ``d`` is a regular-hours trading session."""
        day = _as_date(d)
        return bool(self._schedule_dates(day, day))

    def is_early_close(self, d: date | datetime | str) -> bool:
        """True if ``d`` is a session that closed early (half day).

        A half day is still one session; this flag only lets callers treat its
        truncated bar coverage specially.
        """
        day = _as_date(d)
        schedule = self._cal.schedule(
            start_date=day.isoformat(), end_date=day.isoformat()
        )
        if schedule.empty:
            return False
        return not self._cal.early_closes(schedule).empty

    def nth_session_after(self, session: date | datetime | str, n: int) -> date:
        """Return the session ``n`` sessions at/after ``session``.

        ``n=0`` returns ``session`` itself, which must be a valid session.
        """
        if n < 0:
            raise ValueError(f"n must be non-negative, got {n}")
        anchor = _as_date(session)
        if not self.is_session(anchor):
            raise ValueError(f"{anchor} is not a trading session on {self._name}")
        span = (n + 5) * 3 + 15
        while span <= _MAX_SCAN_DAYS:
            found = self._schedule_dates(anchor, anchor + timedelta(days=span))
            if len(found) > n:
                return found[n]
            span *= 2
        raise ValueError(
            f"Could not locate session #{n} after {anchor}; calendar exhausted"
        )

    def sessions_before(
        self, session: date | datetime | str, n: int
    ) -> tuple[date, ...]:
        """Return up to ``n`` sessions strictly before ``session``, ascending.

        Returns fewer than ``n`` only when calendar history runs out; callers
        that require exactly ``n`` (e.g. warmup) must check the length.
        """
        if n < 0:
            raise ValueError(f"n must be non-negative, got {n}")
        anchor = _as_date(session)
        if n == 0:
            return ()
        found: list[date] = []
        span = n * 3 + 15
        while span <= _MAX_SCAN_DAYS:
            found = [
                d
                for d in self._schedule_dates(anchor - timedelta(days=span), anchor)
                if d < anchor
            ]
            if len(found) >= n:
                return tuple(found[-n:])
            span *= 2
        return tuple(found)

    def reconcile(
        self,
        start: date | datetime | str,
        end: date | datetime | str,
        data_dates: Iterable[date],
    ) -> SessionReconciliation:
        """Reconcile the calendar's expected sessions over a range against data."""
        expected = self.sessions_between(start, end)
        return reconcile_sessions(expected, data_dates, calendar_name=self._name)
