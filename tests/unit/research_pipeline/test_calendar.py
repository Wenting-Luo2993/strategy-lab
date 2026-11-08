"""Tests for the P3 session calendar wrapper.

These pin exact session counts against hand-verified NYSE (XNYS) values so a
regression in the calendar wrapper — or an accidental switch to calendar-day
arithmetic — is caught immediately.
"""

from __future__ import annotations

from datetime import date

import pytest

from vibe.research_pipeline.splits.calendar import (
    SessionCalendar,
    SessionReconciliation,
    reconcile_sessions,
)


@pytest.fixture(scope="module")
def cal() -> SessionCalendar:
    return SessionCalendar("XNYS")


# --------------------------------------------------------------------------
# Session enumeration and counting across known holidays
# --------------------------------------------------------------------------


def test_full_year_2021_has_252_sessions(cal: SessionCalendar):
    """A full NYSE year is 252 sessions; drift here breaks every fold length."""
    assert cal.session_count(date(2021, 1, 1), date(2021, 12, 31)) == 252


def test_thanksgiving_week_excludes_the_holiday(cal: SessionCalendar):
    """Thanksgiving (2021-11-25) is not a session; the half-day after it is."""
    sessions = cal.sessions_between(date(2021, 11, 22), date(2021, 11, 30))
    assert sessions == (
        date(2021, 11, 22),
        date(2021, 11, 23),
        date(2021, 11, 24),
        date(2021, 11, 26),  # half day, still a session
        date(2021, 11, 29),
        date(2021, 11, 30),
    )
    assert date(2021, 11, 25) not in sessions


def test_independence_day_2022_is_not_a_session(cal: SessionCalendar):
    """July 4th 2022 (observed Monday) is closed."""
    assert cal.is_session(date(2022, 7, 4)) is False
    assert cal.session_count(date(2022, 7, 1), date(2022, 7, 8)) == 5


def test_christmas_week_2021_excludes_observed_holiday(cal: SessionCalendar):
    """2021-12-24 is the observed Christmas holiday and is not a session."""
    assert cal.is_session(date(2021, 12, 24)) is False
    assert cal.session_count(date(2021, 12, 20), date(2021, 12, 31)) == 9


def test_weekends_are_not_sessions(cal: SessionCalendar):
    assert cal.is_session(date(2021, 11, 27)) is False  # Saturday
    assert cal.is_session(date(2021, 11, 28)) is False  # Sunday


# --------------------------------------------------------------------------
# Early close (half day) handling
# --------------------------------------------------------------------------


def test_half_day_counts_as_one_session_and_flags_early_close(cal: SessionCalendar):
    """A half day must count as exactly one session, not zero and not two."""
    assert cal.is_session(date(2021, 11, 26)) is True
    assert cal.is_early_close(date(2021, 11, 26)) is True
    # A normal full session is not flagged.
    assert cal.is_early_close(date(2021, 11, 24)) is False
    # A non-session is never an early close.
    assert cal.is_early_close(date(2021, 11, 25)) is False


# --------------------------------------------------------------------------
# nth_session_after / sessions_before land on real sessions
# --------------------------------------------------------------------------


def test_nth_session_after_skips_holiday(cal: SessionCalendar):
    """Stepping forward from the day before Thanksgiving skips the holiday."""
    # 2021-11-24 (Wed) -> +1 session is 2021-11-26 (Fri), 25th is closed.
    assert cal.nth_session_after(date(2021, 11, 24), 1) == date(2021, 11, 26)


def test_nth_session_after_zero_returns_same_session(cal: SessionCalendar):
    assert cal.nth_session_after(date(2021, 11, 24), 0) == date(2021, 11, 24)


def test_nth_session_after_requires_a_real_session(cal: SessionCalendar):
    with pytest.raises(ValueError, match="not a trading session"):
        cal.nth_session_after(date(2021, 11, 25), 1)


def test_sessions_before_returns_real_sessions_in_order(cal: SessionCalendar):
    before = cal.sessions_before(date(2021, 11, 29), 3)
    assert before == (date(2021, 11, 23), date(2021, 11, 24), date(2021, 11, 26))


def test_every_enumerated_session_is_a_weekday_session(cal: SessionCalendar):
    """No boundary a planner picks off this list can be a weekend or holiday."""
    for session in cal.sessions_between(date(2022, 1, 1), date(2022, 3, 31)):
        assert session.weekday() < 5
        assert cal.is_session(session)


# --------------------------------------------------------------------------
# Reconciliation against actual data
# --------------------------------------------------------------------------


def test_reconciliation_detects_missing_and_unexpected(cal: SessionCalendar):
    """A calendar session absent from data and a stray extra date are both flagged.

    A silently missing session would shift every downstream fold boundary, so a
    data hole must surface as a structured result rather than be dropped.
    """
    expected = cal.sessions_between(date(2021, 11, 22), date(2021, 11, 30))
    # Drop a real session (data hole) and add a non-session date.
    data_dates = [d for d in expected if d != date(2021, 11, 24)]
    data_dates.append(date(2021, 11, 25))  # Thanksgiving: not a session

    result = cal.reconcile(date(2021, 11, 22), date(2021, 11, 30), data_dates)

    assert isinstance(result, SessionReconciliation)
    assert result.missing_from_data == (date(2021, 11, 24),)
    assert result.unexpected_in_data == (date(2021, 11, 25),)
    assert result.is_clean is False


def test_reconciliation_clean_when_data_matches(cal: SessionCalendar):
    expected = cal.sessions_between(date(2021, 11, 22), date(2021, 11, 30))
    result = reconcile_sessions(expected, expected, calendar_name="XNYS")
    assert result.is_clean is True
    assert result.missing_from_data == ()
    assert result.unexpected_in_data == ()


def test_reconciliation_result_is_frozen(cal: SessionCalendar):
    result = reconcile_sessions([], [], calendar_name="XNYS")
    with pytest.raises(Exception):
        result.calendar_name = "XLON"
