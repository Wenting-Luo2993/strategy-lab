"""Base market scheduler interface for different trading markets."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional
import pytz


@dataclass
class MarketSession:
    """Represents a trading session."""

    open_time: datetime
    close_time: datetime
    session_name: str = "regular"  # regular, pre, post, etc.

    def is_active(self, dt: datetime) -> bool:
        """Check if datetime falls within this session."""
        return self.open_time <= dt <= self.close_time


class BaseMarketScheduler(ABC):
    """
    Abstract base class for market schedulers.

    Defines the interface that all market schedulers must implement,
    regardless of market type (stocks, forex, crypto).
    """

    def __init__(self, timezone: str = "UTC"):
        """
        Initialize base market scheduler.

        Args:
            timezone: Timezone string (e.g., 'US/Eastern', 'UTC')
        """
        self.timezone = pytz.timezone(timezone)

    @abstractmethod
    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        """
        Check if market is currently open.

        Args:
            dt: Datetime to check (default: now in market timezone)

        Returns:
            True if market is open, False otherwise
        """
        pass

    @abstractmethod
    def get_open_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get market open time for a given date.

        Args:
            date: Date to get open time (default: today)

        Returns:
            Market open time in market timezone, or None if market closed
        """
        pass

    @abstractmethod
    def get_close_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get market close time for a given date.

        Args:
            date: Date to get close time (default: today)

        Returns:
            Market close time in market timezone, or None if market closed
        """
        pass

    @abstractmethod
    def next_market_open(self, from_time: Optional[datetime] = None) -> datetime:
        """
        Get next market open time.

        Args:
            from_time: Starting datetime (default: now)

        Returns:
            Next market open datetime in market timezone
        """
        pass

    @abstractmethod
    def next_market_close(self, from_time: Optional[datetime] = None) -> datetime:
        """
        Get next market close time.

        Args:
            from_time: Starting datetime (default: now)

        Returns:
            Next market close datetime in market timezone
        """
        pass

    @abstractmethod
    def is_valid_trading_day(self, date: datetime) -> bool:
        """
        Check if given date is a valid trading day.

        Args:
            date: Date to check

        Returns:
            True if market is open on this day
        """
        pass

    @abstractmethod
    def get_market_type(self) -> str:
        """
        Get market type identifier.

        Returns:
            Market type string ('stocks', 'forex', 'crypto')
        """
        pass

    @abstractmethod
    def get_session_end_time(self, date: Optional[datetime] = None) -> Optional[datetime]:
        """
        Get session end time for daily summary purposes.

        For 24/7 markets, this might be a logical end-of-day time (e.g., midnight UTC).
        For stocks, this is market close time.

        Args:
            date: Date to get session end (default: today)

        Returns:
            Session end time or None
        """
        pass

    def _ensure_timezone_aware(self, dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware in market timezone."""
        if dt.tzinfo is None:
            return self.timezone.localize(dt)
        return dt.astimezone(self.timezone)
