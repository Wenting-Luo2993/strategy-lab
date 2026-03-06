"""Timezone-aware datetime utilities.

This module provides helper functions for working with timezone-aware datetime
objects in the context of market trading. All functions ensure proper timezone
handling to avoid subtle bugs from naive datetime objects.
"""

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.trading_bot.core.market_schedulers.base import BaseMarketScheduler


def get_market_now(market_scheduler: 'BaseMarketScheduler') -> datetime:
    """Get current time in market timezone.

    This is the preferred way to get "now" in trading bot code, as it ensures
    timezone awareness using the market's configured timezone.

    For MockMarketScheduler, this returns the mock time. For real schedulers,
    this returns the actual current time.

    Args:
        market_scheduler: Market scheduler with timezone info

    Returns:
        Current datetime in market timezone (timezone-aware)

    Example:
        >>> from vibe.trading_bot.utils.datetime_utils import get_market_now
        >>> now = get_market_now(self.market_scheduler)
        >>> # now is timezone-aware (e.g., 2026-02-28 09:30:00-05:00 EST)
    """
    # Use scheduler's now() method if available (e.g., MockMarketScheduler)
    # Otherwise fall back to real time
    if hasattr(market_scheduler, 'now') and callable(getattr(market_scheduler, 'now')):
        return market_scheduler.now()
    return datetime.now(market_scheduler.timezone)


def get_market_date(market_scheduler: 'BaseMarketScheduler') -> str:
    """Get current date in market timezone (ISO format YYYY-MM-DD).

    This is the preferred way to get the current date for daily operations,
    as it ensures the date is calculated in the market's timezone (not UTC
    or local system timezone).

    Args:
        market_scheduler: Market scheduler with timezone info

    Returns:
        Current date string in ISO format (YYYY-MM-DD)

    Example:
        >>> from vibe.trading_bot.utils.datetime_utils import get_market_date
        >>> date = get_market_date(self.market_scheduler)
        >>> # date is "2026-02-28"
    """
    return get_market_now(market_scheduler).date().isoformat()


def format_market_time(
    dt: datetime,
    fmt: str = "%H:%M:%S %Z"
) -> str:
    """Format datetime with timezone info.

    Formats a datetime object using the specified format string. Default format
    includes time and timezone abbreviation (e.g., "09:30:00 EST").

    Args:
        dt: Datetime to format (should be timezone-aware)
        fmt: Format string (default includes time and timezone)

    Returns:
        Formatted datetime string

    Example:
        >>> from vibe.trading_bot.utils.datetime_utils import format_market_time
        >>> formatted = format_market_time(now)
        >>> # formatted is "09:30:00 EST"
        >>>
        >>> # Custom format
        >>> formatted = format_market_time(now, "%Y-%m-%d %H:%M:%S")
        >>> # formatted is "2026-02-28 09:30:00"
    """
    return dt.strftime(fmt)
