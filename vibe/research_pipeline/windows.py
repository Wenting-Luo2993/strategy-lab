"""Canonical parsing of ``--start`` / ``--end`` evaluation window bounds.

Every runner takes a window as two ``YYYY-MM-DD`` strings and hands it to
``BacktestEngine.run``, which filters bars by timestamp. The end bound is the
subtle one: ``--end 2022-06-30`` names a session the operator expects to be
*included*, and the exchange calendar agrees -- ``SessionCalendar.sessions_between``
is inclusive at both ends. Parsing it as midnight excludes every bar of that
day, so the run silently covers one session fewer than requested.

That stayed invisible for as long as nothing compared a run's session count
against an independent source; the run simply agreed with itself. It surfaced
the moment validation was switched on, as a blocking DATA-001 mismatch.

These live here rather than in any one runner because four call sites need the
same answer, and a window that means different things in different scripts is
exactly the kind of drift that makes two runs incomparable.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")

__all__ = ["EXCHANGE_TZ", "parse_window_start", "parse_window_end"]


def parse_window_start(value: str) -> datetime:
    """Parse ``YYYY-MM-DD`` as the first instant of that session's day."""
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=EXCHANGE_TZ)


def parse_window_end(value: str) -> datetime:
    """Parse ``YYYY-MM-DD`` as the *last* instant of that day, not midnight.

    See the module docstring: midnight drops the final trading session.
    """
    return datetime.strptime(value, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=EXCHANGE_TZ
    )
