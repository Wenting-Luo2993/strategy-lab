"""
Abstract base class for data providers.
Used by both backtester and live trading.
"""

from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd


class DataProvider(ABC):
    """
    Abstract interface for data access.
    Strategies request data through this interface.
    """

    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """
        Get last N bars for symbol at timeframe.

        Args:
            symbol: Stock symbol (e.g., 'AAPL')
            timeframe: Bar timeframe (e.g., '1min', '5min', '1D')
            n: Number of bars to retrieve

        Returns:
            DataFrame with OHLCV data
        """
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """
        Get current/latest price for symbol.

        Args:
            symbol: Stock symbol (e.g., 'AAPL')

        Returns:
            Current price
        """
        pass
