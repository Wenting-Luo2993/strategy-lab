"""Forex market scheduler for 24/5 trading."""

from datetime import datetime, timedelta, time
from typing import Optional
import pytz

from .base import BaseMarketScheduler


class ForexMarketScheduler(BaseMarketScheduler):
    """
    Market scheduler for Forex markets.

    Forex markets trade 24 hours a day, 5 days a week.
    Opens Sunday 5:00 PM ET, closes Friday 5:00 PM ET.
    """

    # Forex market hours
    FOREX_OPEN_DAY = 6  # Sunday (0=Monday, 6=Sunday)
    FOREX_OPEN_TIME = time(17, 0)  # 5:00 PM
    FOREX_CLOSE_DAY = 4  # Friday
    FOREX_CLOSE_TIME = time(17, 0)  # 5:00 PM

    def __init__(self, timezone: str = "US/Eastern"):
        """
        Initialize forex market scheduler.

        Args:
            timezone: Timezone string (default: US/Eastern)
        """
        super().__init__(timezone=timezone)

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if forex market is currently open."""
        if dt is None:
            dt = datetime.now(self.timezone)

        dt = self._ensure_timezone_aware(dt)

        # Forex opens Sunday 5pm ET, closes Friday 5pm ET
        day_of_week = dt.weekday()
        current_time = dt.time()

        # Sunday after 5pm
        if day_of_week == self.FOREX_OPEN_DAY and current_time >= self.FOREX_OPEN_TIME:
            return True

        # Monday through Thursday (all day)
        if 0 <= day_of_week <= 3:
            return True

        # Friday before 5pm
        if day_of_week == self.FOREX_CLOSE_DAY and current_time < self.FOREX_CLOSE_TIME:
            return True

        return False

    def get_open_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get forex market open time for the week containing the date."""
        if date is None:
            date = datetime.now(self.timezone)

        date = self._ensure_timezone_aware(date)

        # Find the Sunday of this week
        days_until_sunday = (self.FOREX_OPEN_DAY - date.weekday()) % 7
        if days_until_sunday > 0 or (days_until_sunday == 0 and date.time() >= self.FOREX_OPEN_TIME):
            # Move to previous Sunday
            days_until_sunday -= 7

        sunday = date + timedelta(days=days_until_sunday)
        open_time = self.timezone.localize(
            datetime.combine(sunday.date(), self.FOREX_OPEN_TIME)
        )

        return open_time

    def get_close_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get forex market close time for the week containing the date."""
        if date is None:
            date = datetime.now(self.timezone)

        date = self._ensure_timezone_aware(date)

        # Find the Friday of this week
        days_until_friday = (self.FOREX_CLOSE_DAY - date.weekday()) % 7
        friday = date + timedelta(days=days_until_friday)

        # If we're past Friday's close, move to next Friday
        if friday.date() == date.date() and date.time() >= self.FOREX_CLOSE_TIME:
            friday += timedelta(days=7)

        close_time = self.timezone.localize(
            datetime.combine(friday.date(), self.FOREX_CLOSE_TIME)
        )

        return close_time

    def next_market_open(self, from_time: Optional[datetime] = None) -> datetime:
        """Get next forex market open time."""
        if from_time is None:
            from_time = datetime.now(self.timezone)

        from_time = self._ensure_timezone_aware(from_time)

        # If market is currently open, next open is next Sunday
        if self.is_market_open(from_time):
            # Find next Sunday
            days_until_sunday = (self.FOREX_OPEN_DAY - from_time.weekday()) % 7
            if days_until_sunday == 0:
                days_until_sunday = 7
            next_sunday = from_time + timedelta(days=days_until_sunday)
        else:
            # Market is closed, find next open (this Sunday if before 5pm Sunday, or next Sunday)
            days_until_sunday = (self.FOREX_OPEN_DAY - from_time.weekday()) % 7
            next_sunday = from_time + timedelta(days=days_until_sunday)

            # If it's Sunday but we're past open time, move to next Sunday
            if next_sunday.date() == from_time.date() and from_time.time() >= self.FOREX_OPEN_TIME:
                next_sunday += timedelta(days=7)

        return self.timezone.localize(
            datetime.combine(next_sunday.date(), self.FOREX_OPEN_TIME)
        )

    def next_market_close(self, from_time: Optional[datetime] = None) -> datetime:
        """Get next forex market close time."""
        if from_time is None:
            from_time = datetime.now(self.timezone)

        from_time = self._ensure_timezone_aware(from_time)

        # Find next Friday 5pm
        days_until_friday = (self.FOREX_CLOSE_DAY - from_time.weekday()) % 7
        next_friday = from_time + timedelta(days=days_until_friday)

        # If it's Friday and we're past close, or before Friday, get this Friday
        if next_friday.date() == from_time.date() and from_time.time() >= self.FOREX_CLOSE_TIME:
            # Already past close, move to next Friday
            next_friday += timedelta(days=7)

        return self.timezone.localize(
            datetime.combine(next_friday.date(), self.FOREX_CLOSE_TIME)
        )

    def is_valid_trading_day(self, date: datetime) -> bool:
        """Check if given date is a valid trading day (any day during trading week)."""
        if isinstance(date, datetime):
            dt = date
        else:
            dt = self.timezone.localize(datetime.combine(date, time(12, 0)))

        dt = self._ensure_timezone_aware(dt)
        return self.is_market_open(dt)

    def get_market_type(self) -> str:
        """Get market type identifier."""
        return "forex"

    def get_session_end_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get session end time for daily summary.

        For forex, use Friday 5pm as the logical session end (end of trading week).
        """
        return self.get_close_time(date)
