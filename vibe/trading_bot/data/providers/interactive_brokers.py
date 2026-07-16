"""Interactive Brokers data provider for live quote snapshots."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from vibe.common.models import Bar
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI
from vibe.trading_bot.data.providers.types import ProviderType, RESTDataProvider

logger = logging.getLogger(__name__)


class InteractiveBrokersDataProvider(RESTDataProvider):
    """Polling provider backed by IB live market data snapshots."""

    def __init__(self, broker: InteractiveBrokersAPI, rate_limit_per_minute: int = 120):
        self.broker = broker
        self._rate_limit_per_minute = rate_limit_per_minute
        self._connected = False

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
        self._connected = await self.broker.connect()
        return self._connected

    async def disconnect(self) -> None:
        await self.broker.disconnect()
        self._connected = False

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