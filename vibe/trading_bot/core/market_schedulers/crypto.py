"""Crypto market scheduler for 24/7 trading."""

from datetime import datetime, timedelta, time
from typing import Optional
import pytz

from .base import BaseMarketScheduler


class CryptoMarketScheduler(BaseMarketScheduler):
    """
    Market scheduler for cryptocurrency markets.

    Crypto markets trade 24 hours a day, 7 days a week.
    Never closes (except for exchange maintenance, not handled here).
    """

    # Logical end-of-day time for summaries (midnight UTC)
    EOD_TIME = time(0, 0)

    def __init__(self, timezone: str = "UTC"):
        """
        Initialize crypto market scheduler.

        Args:
            timezone: Timezone string (default: UTC for crypto)
        """
        super().__init__(timezone=timezone)

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if crypto market is open (always True)."""
        return True

    def get_open_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get market open time (returns midnight for the given date).

        Since crypto is 24/7, we return midnight as a logical "open" time.
        """
        if date is None:
            date = datetime.now(self.timezone)

        if isinstance(date, datetime):
            date = date.date()

        return self.timezone.localize(datetime.combine(date, time(0, 0)))

    def get_close_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get market close time (returns end of day for the given date).

        Since crypto is 24/7, we return 23:59:59 as a logical "close" time.
        """
        if date is None:
            date = datetime.now(self.timezone)

        if isinstance(date, datetime):
            date = date.date()

        return self.timezone.localize(datetime.combine(date, time(23, 59, 59)))

    def next_market_open(self, from_time: Optional[datetime] = None) -> datetime:
        """
        Get next market open time.

        Since crypto is 24/7, this returns the next midnight (start of next day).
        """
        if from_time is None:
            from_time = datetime.now(self.timezone)

        from_time = self._ensure_timezone_aware(from_time)

        # Next midnight
        next_day = from_time.date() + timedelta(days=1)
        return self.timezone.localize(datetime.combine(next_day, time(0, 0)))

    def next_market_close(self, from_time: Optional[datetime] = None) -> datetime:
        """
        Get next market close time.

        Since crypto is 24/7, this returns the end of current day (23:59:59).
        """
        if from_time is None:
            from_time = datetime.now(self.timezone)

        from_time = self._ensure_timezone_aware(from_time)

        # End of today
        return self.timezone.localize(
            datetime.combine(from_time.date(), time(23, 59, 59))
        )

    def is_valid_trading_day(self, date: datetime) -> bool:
        """Check if given date is a valid trading day (always True for crypto)."""
        return True

    def get_market_type(self) -> str:
        """Get market type identifier."""
        return "crypto"

    def get_session_end_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get session end time for daily summary.

        For crypto, use midnight UTC as the logical end-of-day.
        """
        if date is None:
            date = datetime.now(self.timezone)

        if isinstance(date, datetime):
            date = date.date()

        # Midnight of the next day
        next_day = date + timedelta(days=1)
        return self.timezone.localize(datetime.combine(next_day, self.EOD_TIME))
