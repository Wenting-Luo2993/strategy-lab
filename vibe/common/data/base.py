"""
Abstract data provider interface for OHLCV data access.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd

from vibe.common.models import Bar


class DataProvider(ABC):
    """
    Abstract base class for data providers.
    Both live and backtest implementations must provide these methods.
    """

    # Standard column names for OHLCV DataFrames
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Get OHLCV bars for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'AAPL')
            timeframe: Timeframe ('1m', '5m', '15m', '1h', '1d', etc.)
            limit: Maximum number of bars to return
            start_time: Start time for bars
            end_time: End time for bars

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        pass

    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        """
        Get current price for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current price as float
        """
        pass

    @abstractmethod
    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        """
        Get the latest bar for a symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Bar object or None if not available
        """
        pass
