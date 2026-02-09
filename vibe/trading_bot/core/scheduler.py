"""Market calendar and scheduler integration."""

from datetime import datetime, timedelta, time
from typing import Optional
import pandas_market_calendars as mcal
import pytz


class MarketScheduler:
    """Manages market hours, holidays, and early closes using pandas_market_calendars."""

    def __init__(self, exchange: str = "NYSE"):
        """Initialize market scheduler.

        Args:
            exchange: Stock exchange name (NYSE, NASDAQ, etc.)

        Raises:
            ValueError: If exchange is not supported
        """
        try:
            self.calendar = mcal.get_calendar(exchange)
        except (ValueError, RuntimeError, KeyError) as e:
            raise ValueError(f"Unsupported exchange: {exchange}") from e

        self.exchange = exchange
        self.timezone = pytz.timezone("US/Eastern")

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if market is currently open.

        Args:
            dt: Datetime to check (default: now in market timezone)

        Returns:
            True if market is open, False otherwise
        """
        if dt is None:
            dt = datetime.now(self.timezone)

        # Ensure datetime is timezone-aware and in market timezone
        if dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)

        # Get today's schedule
        date = dt.date()
        schedule = self.calendar.schedule(start_date=date, end_date=date)

        if schedule.empty:
            # No session scheduled (holiday or weekend)
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
        """Get market open time for a given date.

        Args:
            date: Date to get open time (default: today)

        Returns:
            Market open time in market timezone, or None if market closed
        """
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
        """Get market close time for a given date (handles early closes).

        Args:
            date: Date to get close time (default: today)

        Returns:
            Market close time in market timezone, or None if market closed
        """
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

    def next_market_open(self, dt: Optional[datetime] = None) -> datetime:
        """Get next market open time.

        Args:
            dt: Reference datetime (default: now)

        Returns:
            Next market open time in market timezone
        """
        if dt is None:
            dt = datetime.now(self.timezone)

        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)

        # Start searching from tomorrow
        search_date = (dt.date() + timedelta(days=1))

        # Search up to 30 days in future for next market open
        schedule = self.calendar.schedule(
            start_date=search_date,
            end_date=search_date + timedelta(days=30)
        )

        if schedule.empty:
            raise ValueError("No market open found in next 30 days")

        market_open = schedule.iloc[0]["market_open"]

        if market_open.tzinfo is None:
            market_open = self.timezone.localize(market_open)

        return market_open

    def next_market_close(self, dt: Optional[datetime] = None) -> datetime:
        """Get next market close time.

        Args:
            dt: Reference datetime (default: now)

        Returns:
            Next market close time in market timezone
        """
        if dt is None:
            dt = datetime.now(self.timezone)

        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = self.timezone.localize(dt)
        else:
            dt = dt.astimezone(self.timezone)

        # Get today's close time
        today_close = self.get_close_time(dt.date())

        if today_close is not None and dt < today_close:
            # Market closes later today
            return today_close

        # Find next market close
        search_date = dt.date() + timedelta(days=1)
        schedule = self.calendar.schedule(
            start_date=search_date,
            end_date=search_date + timedelta(days=30)
        )

        if schedule.empty:
            raise ValueError("No market close found in next 30 days")

        market_close = schedule.iloc[0]["market_close"]

        if market_close.tzinfo is None:
            market_close = self.timezone.localize(market_close)

        return market_close

    def is_holiday(self, date: Optional[datetime] = None) -> bool:
        """Check if a date is a market holiday.

        Args:
            date: Date to check (default: today)

        Returns:
            True if market is closed on this date, False otherwise
        """
        if date is None:
            date = datetime.now(self.timezone).date()
        elif isinstance(date, datetime):
            date = date.date()

        schedule = self.calendar.schedule(start_date=date, end_date=date)
        return schedule.empty

    def is_early_close(self, date: Optional[datetime] = None) -> bool:
        """Check if market has early close on a given date.

        NYSE early closes at 1:00 PM (13:00) on the day before Thanksgiving
        and on Christmas Eve (if it falls on a weekday).

        Args:
            date: Date to check (default: today)

        Returns:
            True if market has early close, False otherwise
        """
        if date is None:
            date = datetime.now(self.timezone).date()
        elif isinstance(date, datetime):
            date = date.date()

        # Standard early close time for NYSE
        EARLY_CLOSE_TIME = time(13, 0)  # 1:00 PM

        close_time = self.get_close_time(date)

        if close_time is None:
            return False

        # Compare close times - if before standard close (4:00 PM), it's early
        return close_time.time() < time(16, 0)

    def get_schedule(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 30
    ) -> list:
        """Get market schedule for a date range.

        Args:
            start_date: Start date (default: today)
            end_date: End date (default: start_date + limit days)
            limit: Maximum number of trading days to return

        Returns:
            List of dicts with market_open, market_close, and early_close flag
        """
        if start_date is None:
            start_date = datetime.now(self.timezone).date()
        elif isinstance(start_date, datetime):
            start_date = start_date.date()

        if end_date is None:
            end_date = start_date + timedelta(days=limit)
        elif isinstance(end_date, datetime):
            end_date = end_date.date()

        schedule = self.calendar.schedule(start_date=start_date, end_date=end_date)

        result = []
        for date, row in schedule.iterrows():
            market_open = row["market_open"]
            market_close = row["market_close"]

            # Ensure timezone-aware
            if market_open.tzinfo is None:
                market_open = self.timezone.localize(market_open)
            if market_close.tzinfo is None:
                market_close = self.timezone.localize(market_close)

            # Check for early close
            is_early = market_close.time() < time(16, 0)

            result.append({
                "date": date.date(),
                "market_open": market_open,
                "market_close": market_close,
                "early_close": is_early,
            })

        return result
