"""
Extended data provider types for trading bot.

Defines WebSocket and REST provider interfaces that extend the common DataProvider.
"""

from abc import abstractmethod
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional

import pandas as pd

from vibe.common.data.base import DataProvider


class ProviderType(str, Enum):
    """Type of data provider delivery mechanism."""
    WEBSOCKET = "websocket"  # Push-based, real-time
    REST = "rest"  # Pull-based, polling


class RealtimeDataProvider(DataProvider):
    """
    Extended data provider interface for real-time providers.

    Adds metadata about provider type and capabilities.
    """

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Get the type of this provider (websocket or rest)."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the name of this provider (e.g., 'Finnhub', 'Polygon')."""
        pass

    @property
    @abstractmethod
    def is_real_time(self) -> bool:
        """Does this provider offer true real-time data (vs delayed)?"""
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect/initialize the provider.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect/cleanup the provider."""
        pass

    @abstractmethod
    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        days: int
    ) -> pd.DataFrame:
        """
        Get historical bars for a symbol (for warm-up phase).

        Args:
            symbol: Trading symbol
            timeframe: Timeframe ('1m', '5m', '15m', '1h', etc.)
            days: Number of days of history

        Returns:
            DataFrame with OHLCV data
        """
        pass


class WebSocketDataProvider(RealtimeDataProvider):
    """
    Abstract base class for WebSocket-based data providers.

    WebSocket providers are push-based: they maintain a persistent connection
    and push data via callbacks when available.

    Examples: Finnhub WebSocket, Alpaca WebSocket, IB WebSocket
    """

    @property
    def provider_type(self) -> ProviderType:
        """WebSocket providers are push-based."""
        return ProviderType.WEBSOCKET

    @property
    def is_real_time(self) -> bool:
        """WebSocket providers are typically real-time."""
        return True

    @abstractmethod
    async def subscribe(self, symbol: str) -> bool:
        """
        Subscribe to real-time data for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            True if subscription successful
        """
        pass

    @abstractmethod
    async def unsubscribe(self, symbol: str) -> bool:
        """
        Unsubscribe from a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            True if unsubscription successful
        """
        pass

    @abstractmethod
    def on_trade(self, callback: Callable) -> None:
        """
        Register callback for trade events.

        Args:
            callback: Async function to call when trade received
                Signature: async def callback(trade_data: dict) -> None
                trade_data format:
                {
                    'symbol': str,
                    'price': float,
                    'size': int,
                    'timestamp': datetime,
                    'bid': float (optional),
                    'ask': float (optional)
                }
        """
        pass

    @abstractmethod
    def on_error(self, callback: Callable) -> None:
        """
        Register callback for error events.

        Args:
            callback: Async function to call on errors
                Signature: async def callback(error_data: dict) -> None
        """
        pass

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Is the WebSocket currently connected?"""
        pass


class RESTDataProvider(RealtimeDataProvider):
    """
    Abstract base class for REST API-based data providers.

    REST providers are pull-based: orchestrator polls them at regular
    intervals to fetch latest data.

    Examples: Polygon.io, Yahoo Finance, IB REST API
    """

    @property
    def provider_type(self) -> ProviderType:
        """REST providers are pull-based."""
        return ProviderType.REST

    @abstractmethod
    async def get_latest_bar(
        self,
        symbol: str,
        timeframe: str = "1"
    ) -> Optional[Dict]:
        """
        Get the latest bar for a symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe in minutes (e.g., '1', '5', '15')

        Returns:
            Dict with bar data or None if error:
            {
                'symbol': str,
                'timestamp': datetime,
                'open': float,
                'high': float,
                'low': float,
                'close': float,
                'volume': int
            }
        """
        pass

    @abstractmethod
    async def get_multiple_latest_bars(
        self,
        symbols: List[str],
        timeframe: str = "1"
    ) -> Dict[str, Optional[Dict]]:
        """
        Get latest bars for multiple symbols (batch request).

        Args:
            symbols: List of trading symbols
            timeframe: Timeframe in minutes

        Returns:
            Dict mapping symbol to bar data (or None if error)
        """
        pass

    @property
    @abstractmethod
    def rate_limit_per_minute(self) -> int:
        """Maximum API calls per minute."""
        pass

    @property
    @abstractmethod
    def recommended_poll_interval_seconds(self) -> int:
        """
        Recommended polling interval in seconds.

        This should be calculated based on:
        - Rate limit (e.g., 5 calls/min -> poll every 60/5 = 12s minimum)
        - Number of symbols
        - Real-time requirements

        Example: With 5 symbols and 5 calls/min limit, poll every 60s
        """
        pass
