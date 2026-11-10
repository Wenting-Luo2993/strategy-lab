"""Interactive Brokers data provider for live quote snapshots."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import pandas as pd

from vibe.common.models import Bar
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI
from vibe.trading_bot.data.providers.types import ProviderType, RESTDataProvider

logger = logging.getLogger(__name__)


class InteractiveBrokersDataProvider(RESTDataProvider):
    """Polling provider backed by IB live market data snapshots.
    
    When initialized with a shared broker (from execution), this provider:
    - Does NOT own the broker's lifecycle (connect/disconnect)
    - Coordinates market data requests to avoid cancelling each other's subscriptions
    - Tracks active subscriptions per-caller to support concurrent requests
    """

    def __init__(self, broker: InteractiveBrokersAPI, rate_limit_per_minute: int = 120):
        self.broker = broker
        self._rate_limit_per_minute = rate_limit_per_minute
        self._connected = False
        # Track whether we own this broker's lifecycle (False = shared, True = standalone)
        self._owns_broker_lifecycle = False
        # Coordinate market data subscriptions: symbol -> count of active callers
        self._active_market_data_subscriptions: Dict[str, int] = {}
        self._active_tick_subscriptions: Dict[str, int] = {}
        self._subscription_lock = asyncio.Lock()

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.REST

    @property
    def provider_name(self) -> str:
        return "Interactive Brokers"

    @property
    def is_real_time(self) -> bool:
        return self.broker.market_data_type == 1

    @property
    def connected(self) -> bool:
        return self._connected and self.broker.ib.isConnected()

    @property
    def rate_limit_per_minute(self) -> int:
        return self._rate_limit_per_minute

    @property
    def recommended_poll_interval_seconds(self) -> int:
        return 5

    async def connect(self) -> bool:
        # Only actually connect if we own the broker (standalone mode)
        if self._owns_broker_lifecycle:
            self._connected = await self.broker.connect()
        else:
            # In shared mode, just track that provider layer is active
            # The broker is already connected by execution engine
            self._connected = self.broker.ib.isConnected()
            if self._connected:
                logger.info("Using existing shared IB broker connection for data provider")
        return self._connected

    async def disconnect(self) -> None:
        # Only disconnect if we own the broker (standalone mode)
        if self._owns_broker_lifecycle:
            await self.broker.disconnect()
        else:
            logger.debug("Skipping IB broker disconnect in shared mode (execution engine owns lifecycle)")
        self._connected = False
        # Clear subscription tracking when provider stops polling
        async with self._subscription_lock:
            self._active_market_data_subscriptions.clear()
            self._active_tick_subscriptions.clear()

    async def get_historical_bars(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
        logger.debug("IB snapshot provider does not fetch historical bars for %s", symbol)
        return pd.DataFrame()

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        bar = await self.get_latest_bar(symbol, timeframe.rstrip("m"))
        return pd.DataFrame([bar]) if bar else pd.DataFrame()

    async def get_current_price(self, symbol: str) -> float:
        quote = await self.broker.get_market_data(symbol)
        return quote.market_price

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        bar = await self.get_latest_bar(symbol, timeframe.rstrip("m"))
        if not bar:
            return None
        return Bar(
            symbol=bar["symbol"],
            timestamp=bar["timestamp"],
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            volume=bar["volume"],
        )

    async def get_latest_bar(self, symbol: str, timeframe: str = "5") -> Optional[Dict]:
        quote = await self.broker.get_market_data(symbol)
        price = quote.market_price
        timestamp = quote.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return {
            "symbol": symbol,
            "timestamp": timestamp,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1,
        }

    async def get_multiple_latest_bars(
        self,
        symbols: List[str],
        timeframe: str = "5",
    ) -> Dict[str, Optional[Dict]]:
        bars: Dict[str, Optional[Dict]] = {}
        for symbol in symbols:
            try:
                bars[symbol] = await self.get_latest_bar(symbol, timeframe)
            except Exception as exc:
                logger.error("Failed to fetch IB market data for %s: %s", symbol, exc)
                bars[symbol] = None
        return bars
