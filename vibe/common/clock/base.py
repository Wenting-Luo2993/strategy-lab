"""
Abstract clock interface for time management.
"""

from abc import ABC, abstractmethod
from datetime import datetime


class Clock(ABC):
    """
    Abstract base class for clock implementations.
    Enables separation between live trading and backtesting time.
    """

    @abstractmethod
    def now(self) -> datetime:
        """
        Get current time.

        Returns:
            Current datetime
        """
        pass

    @abstractmethod
    def is_market_open(self) -> bool:
        """
        Check if market is currently open.

        Returns:
            True if market is open, False otherwise
        """
        pass
