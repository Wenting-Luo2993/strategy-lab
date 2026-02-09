"""
Yahoo Finance data provider for historical OHLCV data.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from .base import LiveDataProvider
from vibe.common.data import DataProvider
from vibe.common.models import Bar


logger = logging.getLogger(__name__)


class YahooDataProvider(LiveDataProvider):
    """
    Data provider for Yahoo Finance historical data.

    Uses yfinance library to fetch OHLCV data with rate limiting,
    retry logic, and error handling.
    """

    # Valid period values for yfinance
    VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}

    # Valid interval values for yfinance
    VALID_INTERVALS = {"1m", "5m", "15m", "30m", "60m", "1h", "1d", "1wk", "1mo"}

    def __init__(
        self,
        rate_limit: float = 5.0,
        rate_limit_period: float = 1.0,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
    ):
        """
        Initialize Yahoo Finance data provider.

        Args:
            rate_limit: Requests per period (default 5 req/sec)
            rate_limit_period: Period for rate limiting
            max_retries: Maximum retry attempts
            retry_backoff_base: Initial backoff in seconds
            retry_backoff_multiplier: Backoff multiplier
        """
        super().__init__(
            rate_limit=rate_limit,
            rate_limit_period=rate_limit_period,
            max_retries=max_retries,
            retry_backoff_base=retry_backoff_base,
            retry_backoff_multiplier=retry_backoff_multiplier,
        )

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Get historical OHLCV bars from Yahoo Finance.

        Args:
            symbol: Trading symbol (e.g., 'AAPL')
            timeframe: Timeframe ('1m', '5m', '15m', '1h', '1d', etc.)
            limit: Not used for Yahoo Finance (uses period instead)
            start_time: Start time for bars
            end_time: End time for bars

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume

        Raises:
            ValueError: If symbol is invalid or timeframe is not supported
            Exception: If fetch fails after all retries
        """
        # Validate inputs
        if not symbol:
            raise ValueError("Symbol cannot be empty")

        if timeframe not in self.VALID_INTERVALS:
            raise ValueError(f"Invalid timeframe {timeframe}. Valid: {self.VALID_INTERVALS}")

        # Use get_historical as the main method
        return await self.get_historical(
            symbol=symbol,
            period=None,
            interval=timeframe,
            start_time=start_time,
            end_time=end_time,
        )

    def _fetch_historical_sync(
        self,
        symbol: str,
        period: Optional[str],
        interval: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> pd.DataFrame:
        """
        Synchronous helper to fetch data from yfinance.

        This runs in a thread pool executor to avoid blocking the event loop.
        """
        ticker = yf.Ticker(symbol)

        if start_time and end_time:
            # Use start and end dates
            df = ticker.history(
                start=start_time.date() if isinstance(start_time, datetime) else start_time,
                end=end_time.date() if isinstance(end_time, datetime) else end_time,
                interval=interval,
            )
        else:
            # Use period
            df = ticker.history(period=period or "1mo", interval=interval)

        return df

    async def get_historical(
        self,
        symbol: str,
        period: Optional[str] = "1mo",
        interval: str = "5m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Get historical data for a symbol with configurable period and interval.

        Args:
            symbol: Trading symbol (e.g., 'AAPL')
            period: Period for data ('1d', '5d', '1mo', '3mo', '6mo', '1y', etc.)
                   If start_time/end_time provided, this is ignored
            interval: Interval for bars ('1m', '5m', '15m', '1h', '1d', etc.)
            start_time: Start datetime (overrides period if provided)
            end_time: End datetime (overrides period if provided)

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
            Index will be timestamp (DatetimeIndex)

        Raises:
            ValueError: If inputs are invalid
            Exception: If fetch fails
        """
        # Validate symbol
        if not symbol or not isinstance(symbol, str):
            raise ValueError("Symbol must be a non-empty string")

        symbol = symbol.upper().strip()

        # Validate interval
        if interval not in self.VALID_INTERVALS:
            raise ValueError(
                f"Invalid interval '{interval}'. Valid: {self.VALID_INTERVALS}"
            )

        # Validate period (if not using start_time/end_time)
        if start_time is None and end_time is None:
            if period and period not in self.VALID_PERIODS:
                raise ValueError(
                    f"Invalid period '{period}'. Valid: {self.VALID_PERIODS}"
                )

        # Apply rate limiting
        await self._apply_rate_limit()

        # Fetch data with retry
        async def fetch():
            try:
                # Use run_in_executor to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                df = await loop.run_in_executor(
                    None,
                    self._fetch_historical_sync,
                    symbol,
                    period,
                    interval,
                    start_time,
                    end_time,
                )

                # Check if we got data
                if df.empty:
                    logger.warning(f"No data returned for {symbol} with period={period}, interval={interval}")
                    return pd.DataFrame()

                # Standardize column names to lowercase
                df.columns = [col.lower() for col in df.columns]

                # Ensure required columns exist
                required_cols = {self.OPEN, self.HIGH, self.LOW, self.CLOSE, self.VOLUME}
                available_cols = set(df.columns)

                if not required_cols.issubset(available_cols):
                    missing = required_cols - available_cols
                    raise ValueError(f"Missing required columns: {missing}")

                # Keep only required columns and reset index to have timestamp as column
                df = df[[self.OPEN, self.HIGH, self.LOW, self.CLOSE, self.VOLUME]].copy()

                # Reset index to make timestamp a column
                if df.index.name is not None:
                    df = df.reset_index()
                    df.rename(columns={df.columns[0]: "timestamp"}, inplace=True)
                else:
                    # Add timestamp column if index doesn't have a name
                    df["timestamp"] = df.index
                    df = df.reset_index(drop=True)

                # Ensure timestamp is datetime
                if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
                    df["timestamp"] = pd.to_datetime(df["timestamp"])

                # Filter by start_time and end_time if provided and not already filtered
                if start_time:
                    df = df[df["timestamp"] >= pd.Timestamp(start_time)]
                if end_time:
                    df = df[df["timestamp"] <= pd.Timestamp(end_time)]

                # Sort by timestamp ascending
                df = df.sort_values("timestamp").reset_index(drop=True)

                logger.info(
                    f"Fetched {len(df)} bars for {symbol} ({interval}) "
                    f"from {df['timestamp'].min()} to {df['timestamp'].max()}"
                )

                return df

            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {str(e)}")
                raise

        return await self._retry_with_backoff(fetch)

    async def get_current_price(self, symbol: str) -> float:
        """
        Get current price for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current price as float

        Raises:
            ValueError: If symbol is invalid
            Exception: If fetch fails
        """
        if not symbol:
            raise ValueError("Symbol cannot be empty")

        await self._apply_rate_limit()

        def fetch_sync():
            ticker = yf.Ticker(symbol.upper().strip())
            data = ticker.history(period="1d")
            if data.empty:
                raise ValueError(f"No data found for symbol {symbol}")
            return float(data["Close"].iloc[-1])

        async def fetch():
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fetch_sync)

        return await self._retry_with_backoff(fetch)  # Pass callable, not coroutine

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        """
        Get the latest bar for a symbol.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Bar object with latest bar data or None if not available
        """
        try:
            df = await self.get_historical(symbol, period="1d", interval=timeframe)
            if df.empty:
                return None

            latest = df.iloc[-1]
            return Bar(
                timestamp=latest["timestamp"],
                open=latest["open"],
                high=latest["high"],
                low=latest["low"],
                close=latest["close"],
                volume=latest["volume"],
            )
        except Exception as e:
            logger.error(f"Error getting latest bar for {symbol}: {str(e)}")
            return None
