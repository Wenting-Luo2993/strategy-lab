"""Stock market scheduler using pandas_market_calendars."""

from datetime import datetime, timedelta
from typing import Optional
import pandas_market_calendars as mcal
import pytz

from .base import BaseMarketScheduler


class StockMarketScheduler(BaseMarketScheduler):
    """
    Market scheduler for stock exchanges.

    Uses pandas_market_calendars for accurate handling of holidays,
    early closes, and exchange-specific trading hours.
    """

    def __init__(self, exchange: str = "NYSE", timezone: str = "US/Eastern"):
        """
        Initialize stock market scheduler.

        Args:
            exchange: Stock exchange name (NYSE, NASDAQ, etc.)
            timezone: Timezone string (default: US/Eastern)

        Raises:
            ValueError: If exchange is not supported
        """
        super().__init__(timezone=timezone)

        try:
            self.calendar = mcal.get_calendar(exchange)
        except (ValueError, RuntimeError, KeyError) as e:
            raise ValueError(f"Unsupported exchange: {exchange}") from e

        self.exchange = exchange

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if market is currently open."""
        if dt is None:
            dt = datetime.now(self.timezone)

        dt = self._ensure_timezone_aware(dt)

        # Get today's schedule
        date = dt.date()
        schedule = self.calendar.schedule(start_date=date, end_date=date)

        if schedule.empty:
            return False

        # Get market open and close times
        market_open = schedule.iloc[0]["market_open"]
        market_close = schedule.iloc[0]["market_close"]

        # Ensure times are timezone-aware
        if market_open.tzinfo is None:
            market_open = self.timezone.localize(market_open)
        if market_close.tzinfo is None:
            market_close = self.timezone.localize(market_close)

        return market_open <= dt <= market_close

    def get_open_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get market open time for a given date."""
        if date is None:
            date = datetime.now(self.timezone).date()
        elif isinstance(date, datetime):
            date = date.date()

        schedule = self.calendar.schedule(start_date=date, end_date=date)

        if schedule.empty:
            return None

        market_open = schedule.iloc[0]["market_open"]
        if market_open.tzinfo is None:
            market_open = self.timezone.localize(market_open)

        return market_open

    def get_close_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get market close time for a given date."""
        if date is None:
            date = datetime.now(self.timezone).date()
        elif isinstance(date, datetime):
            date = date.date()

        schedule = self.calendar.schedule(start_date=date, end_date=date)

        if schedule.empty:
            return None

        market_close = schedule.iloc[0]["market_close"]
        if market_close.tzinfo is None:
            market_close = self.timezone.localize(market_close)

        return market_close

    def next_market_open(self, from_time: Optional[datetime] = None) -> datetime:
        """Get next market open time."""
        if from_time is None:
            from_time = datetime.now(self.timezone)

        from_time = self._ensure_timezone_aware(from_time)

        # Check if market is open today and we're before open
        today_open = self.get_open_time(from_time)
        if today_open and from_time < today_open:
            return today_open

        # Otherwise find next trading day
        search_date = from_time.date() + timedelta(days=1)
        max_days = 10  # Don't search more than 10 days ahead

        for _ in range(max_days):
            open_time = self.get_open_time(search_date)
            if open_time:
                return open_time
            search_date += timedelta(days=1)

        # Fallback: return next weekday 9:30 AM
        next_day = from_time + timedelta(days=1)
        while next_day.weekday() >= 5:  # Skip weekend
            next_day += timedelta(days=1)

        return self.timezone.localize(
            datetime.combine(next_day.date(), datetime.strptime("09:30", "%H:%M").time())
        )

    def next_market_close(self, from_time: Optional[datetime] = None) -> datetime:
        """Get next market close time."""
        if from_time is None:
            from_time = datetime.now(self.timezone)

        from_time = self._ensure_timezone_aware(from_time)

        # Check if market is open today and we're before close
        today_close = self.get_close_time(from_time)
        if today_close and from_time < today_close:
            return today_close

        # Otherwise find next trading day's close
        search_date = from_time.date() + timedelta(days=1)
        max_days = 10

        for _ in range(max_days):
            close_time = self.get_close_time(search_date)
            if close_time:
                return close_time
            search_date += timedelta(days=1)

        # Fallback
        next_day = from_time + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)

        return self.timezone.localize(
            datetime.combine(next_day.date(), datetime.strptime("16:00", "%H:%M").time())
        )

    def is_valid_trading_day(self, date: datetime) -> bool:
        """Check if given date is a valid trading day."""
        if isinstance(date, datetime):
            date = date.date()

        schedule = self.calendar.schedule(start_date=date, end_date=date)
        return not schedule.empty

    def get_market_type(self) -> str:
        """Get market type identifier."""
        return "stocks"

    def get_session_end_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get session end time (market close) for daily summary."""
        return self.get_close_time(date)
