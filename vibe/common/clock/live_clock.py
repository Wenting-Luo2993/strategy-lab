"""
Live clock implementation for real-time trading.
"""

from datetime import datetime

from vibe.common.clock.base import Clock
from vibe.common.clock.market_hours import is_market_open


class LiveClock(Clock):
    """
    Clock implementation that uses real system time.
    Used for live trading.
    """

    def now(self) -> datetime:
        """
        Get current system time.

        Returns:
            Current datetime
        """
        return datetime.now()

    def is_market_open(self) -> bool:
        """
        Check if market is currently open.

        Returns:
            True if market is open, False otherwise
        """
        return is_market_open(datetime.now())
