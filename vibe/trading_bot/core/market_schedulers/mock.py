"""Mock market scheduler with controllable time for testing."""

from datetime import datetime, time, timedelta
from typing import Optional
import pytz

from .base import BaseMarketScheduler


class MockMarketScheduler(BaseMarketScheduler):
    """
    Mock market scheduler with controllable time for testing.

    Allows tests to set arbitrary times and advance time programmatically,
    enabling testing of time-dependent behavior without waiting for real time.

    Example:
        scheduler = MockMarketScheduler()
        scheduler.set_time(9, 25)  # Set to 9:25 AM (warmup phase)
        assert scheduler.is_warmup_phase()

        scheduler.set_time(9, 30)  # Set to 9:30 AM (market open)
        assert scheduler.is_market_open()

        scheduler.advance_time(hours=6, minutes=30)  # Advance to 4:00 PM
        assert not scheduler.is_market_open()
    """

    # Default market hours (US stock market times)
    DEFAULT_OPEN_TIME = time(9, 30)  # 9:30 AM EST
    DEFAULT_CLOSE_TIME = time(16, 0)  # 4:00 PM EST

    def __init__(
        self,
        initial_date: Optional[datetime] = None,
        timezone: str = "America/New_York",
        open_time: time = DEFAULT_OPEN_TIME,
        close_time: time = DEFAULT_CLOSE_TIME
    ):
        """Initialize mock scheduler with controllable time.

        Args:
            initial_date: Starting date/time (default: now in market timezone)
            timezone: Market timezone (default: America/New_York)
            open_time: Market open time (default: 9:30 AM)
            close_time: Market close time (default: 4:00 PM)
        """
        super().__init__(timezone=timezone)

        # Initialize current time
        if initial_date is None:
            self._current_time = datetime.now(self.timezone)
        else:
            self._current_time = self._ensure_timezone_aware(initial_date)

        # Configurable market hours
        self.open_time = open_time
        self.close_time = close_time

    def set_time(self, hour: int, minute: int = 0, second: int = 0) -> None:
        """Set current mock time (same day, different time).

        Args:
            hour: Hour of day (0-23)
            minute: Minute (0-59)
            second: Second (0-59)
        """
        self._current_time = self._current_time.replace(
            hour=hour, minute=minute, second=second, microsecond=0
        )

    def set_date(self, year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> None:
        """Set current mock date and time.

        Args:
            year: Year
            month: Month (1-12)
            day: Day (1-31)
            hour: Hour of day (default: 9)
            minute: Minute (default: 0)
        """
        self._current_time = self.timezone.localize(
            datetime(year, month, day, hour, minute)
        )

    def advance_time(self, **kwargs) -> None:
        """Advance time by a timedelta.

        Args:
            **kwargs: Keyword arguments for timedelta (days, hours, minutes, seconds, etc.)

        Example:
            scheduler.advance_time(hours=1, minutes=30)  # Advance 1.5 hours
            scheduler.advance_time(days=1)  # Advance to next day
        """
        self._current_time += timedelta(**kwargs)

    def now(self) -> datetime:
        """Get current mock time (for compatibility with existing code).

        Returns:
            Current mock time in market timezone
        """
        return self._current_time

    def is_warmup_phase(self, dt: Optional[datetime] = None) -> bool:
        """Check if we're in pre-market warm-up phase (5 minutes before open).

        Override base class to use mock time when dt is None.

        Args:
            dt: Datetime to check (default: current mock time)

        Returns:
            True if in warm-up phase, False otherwise
        """
        if dt is None:
            dt = self._current_time  # Use mock time, not real time!

        dt = self._ensure_timezone_aware(dt)

        warmup_time = self.get_warmup_time(dt)
        open_time = self.get_open_time(dt)

        if warmup_time is None or open_time is None:
            return False

        return warmup_time <= dt < open_time

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """Check if market is currently open.

        Args:
            dt: Datetime to check (default: current mock time)

        Returns:
            True if market is open, False otherwise
        """
        if dt is None:
            dt = self._current_time

        dt = self._ensure_timezone_aware(dt)
        current_time = dt.time()

        return self.open_time <= current_time < self.close_time

    def should_bot_be_active(self, dt: Optional[datetime] = None) -> bool:
        """Check if bot should be active (warm-up phase OR market open).

        Override base class to use mock time when dt is None.

        Args:
            dt: Datetime to check (default: current mock time)

        Returns:
            True if bot should be running, False otherwise
        """
        if dt is None:
            dt = self._current_time  # Use mock time!

        return self.is_warmup_phase(dt) or self.is_market_open(dt)

    def get_open_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get market open time for a given date.

        Args:
            date: Date to get open time (default: current mock date)

        Returns:
            Market open time in market timezone
        """
        if date is None:
            date = self._current_time

        dt = self._ensure_timezone_aware(date)
        return dt.replace(
            hour=self.open_time.hour,
            minute=self.open_time.minute,
            second=0,
            microsecond=0
        )

    def get_close_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get market close time for a given date.

        Args:
            date: Date to get close time (default: current mock date)

        Returns:
            Market close time in market timezone
        """
        if date is None:
            date = self._current_time

        dt = self._ensure_timezone_aware(date)
        return dt.replace(
            hour=self.close_time.hour,
            minute=self.close_time.minute,
            second=0,
            microsecond=0
        )

    def next_market_open(self, from_time: Optional[datetime] = None) -> datetime:
        """Get next market open time.

        Args:
            from_time: Starting datetime (default: current mock time)

        Returns:
            Next market open datetime
        """
        if from_time is None:
            from_time = self._current_time

        from_time = self._ensure_timezone_aware(from_time)

        # If before today's open, return today's open
        today_open = self.get_open_time(from_time)
        if from_time < today_open:
            return today_open

        # Otherwise, return tomorrow's open
        tomorrow = from_time + timedelta(days=1)
        return self.get_open_time(tomorrow)

    def next_market_close(self, from_time: Optional[datetime] = None) -> datetime:
        """Get next market close time.

        Args:
            from_time: Starting datetime (default: current mock time)

        Returns:
            Next market close datetime
        """
        if from_time is None:
            from_time = self._current_time

        from_time = self._ensure_timezone_aware(from_time)

        # If before today's close, return today's close
        today_close = self.get_close_time(from_time)
        if from_time < today_close:
            return today_close

        # Otherwise, return tomorrow's close
        tomorrow = from_time + timedelta(days=1)
        return self.get_close_time(tomorrow)

    def is_valid_trading_day(self, date: datetime) -> bool:
        """Check if given date is a valid trading day.

        For mock scheduler, all days are valid trading days (no holiday logic).

        Args:
            date: Date to check

        Returns:
            True (always, for simplicity in testing)
        """
        return True

    def get_market_type(self) -> str:
        """Get market type identifier.

        Returns:
            'mock' to indicate this is a mock scheduler
        """
        return "mock"

    def get_session_end_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """Get session end time (same as market close for stocks).

        Args:
            date: Date to get session end (default: current mock date)

        Returns:
            Session end time (market close time)
        """
        return self.get_close_time(date)
