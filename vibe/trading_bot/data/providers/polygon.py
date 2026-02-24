"""
Massive (formerly Polygon.io) data provider for real-time stock data.

Free tier: 5 API calls per minute
https://polygon.io/ -> https://massive.com/
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp
import pandas as pd
import pytz

from .types import RESTDataProvider, ProviderType
from vibe.common.models import Bar

logger = logging.getLogger(__name__)


class PolygonDataProvider(RESTDataProvider):
    """
    Massive (formerly Polygon.io) REST API client for real-time stock data.

    Free tier limitations:
    - 5 API calls per minute
    - 15-minute delayed data (free tier)
    - OR Real-time data (paid tier: $29+/month)

    Note: For free tier, this provides same 15-min delay as Yahoo Finance,
    but with better reliability and more accurate data.

    API Format: https://api.massive.com/v2/aggs/ticker/{symbol}/range/{timeframe}/minute/{from}/{to}
    """

    BASE_URL = "https://api.massive.com"

    def __init__(self, api_key: str, rate_limit_per_minute: int = 5):
        """
        Initialize Polygon.io data provider.

        Args:
            api_key: Polygon.io API key
            rate_limit_per_minute: Max API calls per minute (default: 5 for free tier)
        """
        self.api_key = api_key
        self.rate_limit = rate_limit_per_minute
        self.session: Optional[aiohttp.ClientSession] = None

        # Rate limiting state
        self._request_timestamps: List[datetime] = []
        self._rate_limit_lock = asyncio.Lock()

    # RealtimeDataProvider interface implementation
    @property
    def provider_type(self) -> ProviderType:
        """REST API provider."""
        return ProviderType.REST

    @property
    def provider_name(self) -> str:
        """Provider name."""
        return "Massive (Polygon.io)"

    @property
    def is_real_time(self) -> bool:
        """Free tier is 15-min delayed, paid tier is real-time."""
        return False  # Free tier

    @property
    def rate_limit_per_minute(self) -> int:
        """Rate limit for this provider."""
        return self.rate_limit

    @property
    def recommended_poll_interval_seconds(self) -> int:
        """
        Recommended poll interval in seconds.

        With 5 calls/min for 5 symbols = poll every 60 seconds.
        """
        return 60

    async def connect(self) -> bool:
        """Initialize the provider (create session)."""
        await self._ensure_session()
        return True

    async def disconnect(self) -> None:
        """Disconnect and cleanup."""
        await self.close()

    # Common DataProvider interface (from vibe.common.data.base)
    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """Get historical bars (delegates to get_historical_bars)."""
        days = 7 if not start_time else (datetime.now(pytz.UTC) - start_time).days + 1
        return await self.get_historical_bars(symbol, timeframe.rstrip('m'), days)

    async def get_current_price(self, symbol: str) -> float:
        """Get current price from latest bar."""
        bar = await self.get_latest_bar(symbol, timeframe="1")
        return bar["close"] if bar else 0.0

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        """Get latest bar as Bar model."""
        bar_dict = await self.get_latest_bar(symbol, timeframe.rstrip('m'))
        if not bar_dict:
            return None
        return Bar(
            symbol=bar_dict["symbol"],
            timestamp=bar_dict["timestamp"],
            open=bar_dict["open"],
            high=bar_dict["high"],
            low=bar_dict["low"],
            close=bar_dict["close"],
            volume=bar_dict["volume"]
        )

    async def _ensure_session(self):
        """Create aiohttp session if not exists."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close(self):
        """Close aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def _wait_for_rate_limit(self):
        """Wait if necessary to respect rate limit."""
        async with self._rate_limit_lock:
            now = datetime.now()

            # Remove timestamps older than 1 minute
            self._request_timestamps = [
                ts for ts in self._request_timestamps
                if (now - ts).total_seconds() < 60
            ]

            # If at rate limit, wait until oldest request is 60s old
            if len(self._request_timestamps) >= self.rate_limit:
                oldest = self._request_timestamps[0]
                wait_time = 60 - (now - oldest).total_seconds()
                if wait_time > 0:
                    logger.warning(
                        f"Rate limit reached ({self.rate_limit}/min), "
                        f"waiting {wait_time:.1f}s"
                    )
                    await asyncio.sleep(wait_time)

                    # Remove the oldest after waiting
                    self._request_timestamps.pop(0)

            # Record this request
            self._request_timestamps.append(now)

    async def get_latest_bar(
        self,
        symbol: str,
        timeframe: str = "5"  # 5 minute bars (free tier supports 5min, not 1min)
    ) -> Optional[Dict]:
        """
        Get the latest bar for a symbol.

        Args:
            symbol: Stock symbol (e.g., 'AAPL')
            timeframe: Timeframe in minutes (default: '5' for 5-minute bars - free tier limit)

        Returns:
            Dict with bar data or None if error
            {
                'symbol': 'AAPL',
                'timestamp': datetime,
                'open': 150.0,
                'high': 151.0,
                'low': 149.5,
                'close': 150.5,
                'volume': 1000000
            }
        """
        await self._ensure_session()
        await self._wait_for_rate_limit()

        # Get data from previous 2 hours to ensure we get at least one 5-minute bar
        to_time = datetime.now(pytz.UTC)
        from_time = to_time - timedelta(hours=2)

        # Format: /v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from}/{to}
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range/"
            f"{timeframe}/minute/"
            f"{from_time.strftime('%Y-%m-%d')}/{to_time.strftime('%Y-%m-%d')}"
        )

        params = {
            "apiKey": self.api_key,
            "adjusted": "true",
            "sort": "desc",  # Most recent first
            "limit": 1  # Only get latest bar
        }

        try:
            async with self.session.get(url, params=params, timeout=10) as response:
                if response.status == 429:
                    logger.error("Polygon.io rate limit exceeded (HTTP 429)")
                    return None

                if response.status != 200:
                    text = await response.text()
                    logger.error(
                        f"Polygon.io API error for {symbol}: "
                        f"HTTP {response.status} - {text}"
                    )
                    return None

                data = await response.json()

                if data.get("status") != "OK":
                    logger.warning(
                        f"Polygon.io returned status {data.get('status')} for {symbol}"
                    )
                    return None

                results = data.get("results", [])
                if not results:
                    logger.warning(f"No data returned from Polygon.io for {symbol}")
                    return None

                bar = results[0]  # Most recent bar

                return {
                    "symbol": symbol,
                    "timestamp": datetime.fromtimestamp(bar["t"] / 1000, tz=pytz.UTC),
                    "open": bar["o"],
                    "high": bar["h"],
                    "low": bar["l"],
                    "close": bar["c"],
                    "volume": bar["v"]
                }

        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching data from Polygon.io for {symbol}")
            return None
        except Exception as e:
            logger.error(f"Error fetching from Polygon.io for {symbol}: {e}")
            return None

    async def get_multiple_latest_bars(
        self,
        symbols: List[str],
        timeframe: str = "1"
    ) -> Dict[str, Optional[Dict]]:
        """
        Get latest bars for multiple symbols.

        Args:
            symbols: List of stock symbols
            timeframe: Timeframe in minutes

        Returns:
            Dict mapping symbol to bar data
        """
        tasks = [
            self.get_latest_bar(symbol, timeframe)
            for symbol in symbols
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            symbol: result if not isinstance(result, Exception) else None
            for symbol, result in zip(symbols, results)
        }

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "5",  # 5 minute bars
        days: int = 7
    ) -> pd.DataFrame:
        """
        Get historical bars for a symbol.

        Args:
            symbol: Stock symbol
            timeframe: Timeframe in minutes (1, 5, 15, 30, 60)
            days: Number of days of history

        Returns:
            DataFrame with OHLCV data
        """
        await self._ensure_session()
        await self._wait_for_rate_limit()

        to_time = datetime.now(pytz.UTC)
        from_time = to_time - timedelta(days=days)

        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol}/range/"
            f"{timeframe}/minute/"
            f"{from_time.strftime('%Y-%m-%d')}/{to_time.strftime('%Y-%m-%d')}"
        )

        params = {
            "apiKey": self.api_key,
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000  # Max per request
        }

        try:
            async with self.session.get(url, params=params, timeout=30) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.error(
                        f"Polygon.io API error: HTTP {response.status} - {text}"
                    )
                    return pd.DataFrame()

                data = await response.json()

                if data.get("status") != "OK":
                    return pd.DataFrame()

                results = data.get("results", [])
                if not results:
                    return pd.DataFrame()

                # Convert to DataFrame
                df = pd.DataFrame(results)
                df = df.rename(columns={
                    "t": "timestamp",
                    "o": "open",
                    "h": "high",
                    "l": "low",
                    "c": "close",
                    "v": "volume"
                })

                # Convert timestamp from milliseconds to datetime
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

                # Convert to US/Eastern timezone
                df["timestamp"] = df["timestamp"].dt.tz_convert("US/Eastern")

                # Set timestamp as index
                df = df.set_index("timestamp")

                # Select only OHLCV columns
                df = df[["open", "high", "low", "close", "volume"]]

                logger.info(
                    f"Fetched {len(df)} bars for {symbol} ({timeframe}m) "
                    f"from {df.index[0]} to {df.index[-1]}"
                )

                return df

        except Exception as e:
            logger.error(f"Error fetching historical data from Polygon.io: {e}")
            return pd.DataFrame()
