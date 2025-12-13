"""
Finnhub DataLoader - Live WebSocket + REST API Fallback

Integrates FinnhubWebSocketClient and BarAggregator with the DataLoader interface.
Supports both live streaming (WebSocket) and historical data (REST API).
"""

import asyncio
import pandas as pd
import finnhub
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import pytz
from pathlib import Path

from .base import DataLoader, register_loader
from .finnhub_websocket import FinnhubWebSocketClient, BarAggregator
from src.config.finnhub_config_loader import load_finnhub_config
from src.utils.logger import get_logger

logger = get_logger("FinnhubLoader")


@register_loader("finnhub")
class FinnhubWebSocketLoader(DataLoader):
    """
    DataLoader for Finnhub with WebSocket (live) and REST API (historical) support.

    Modes:
    - Live: WebSocket streaming with bar aggregation
    - Historical: REST API for candle data
    - Hybrid: REST for historical + WebSocket for recent bars
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        mode: str = "live",
        auto_connect: bool = False
    ):
        """
        Initialize Finnhub loader.

        Args:
            config_path: Path to finnhub_config.json (None = use default)
            mode: "live" (WebSocket), "historical" (REST), or "hybrid"
            auto_connect: Automatically connect WebSocket on initialization
        """
        # Load configuration
        if config_path:
            self.config = load_finnhub_config(Path(config_path))
        else:
            self.config = load_finnhub_config()

        self.mode = mode
        self.api_key = self.config.api_key

        # Initialize REST API client (for historical data)
        self.finnhub_client = finnhub.Client(api_key=self.api_key)

        # Initialize WebSocket client (for live data)
        self.ws_client: Optional[FinnhubWebSocketClient] = None
        self.bar_aggregator: Optional[BarAggregator] = None
        self._connected = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Subscribed symbols tracking
        self._subscribed_symbols: set = set()

        if auto_connect and mode in ["live", "hybrid"]:
            self._initialize_websocket()

        logger.info(f"FinnhubWebSocketLoader initialized in {mode} mode")

    def _initialize_websocket(self):
        """Initialize WebSocket client and bar aggregator."""
        if self.ws_client is not None:
            logger.warning("WebSocket already initialized")
            return

        # Create bar aggregator
        self.bar_aggregator = BarAggregator(
            bar_interval=self.config.bar_interval,
            timezone=self.config.market_hours.timezone,
            bar_delay_seconds=self.config.bar_delay_seconds
        )

        # Create WebSocket client with aggregator callback
        def message_callback(message: Dict[str, Any]):
            """Handle WebSocket messages and feed to aggregator."""
            if message.get("type") == "trade":
                trades = message.get("data", [])
                for trade in trades:
                    self.bar_aggregator.add_trade(trade)

        self.ws_client = FinnhubWebSocketClient(
            api_key=self.api_key,
            websocket_url=self.config.websocket_url,
            message_callback=message_callback
        )

        logger.info("WebSocket client initialized")

    def connect(self) -> bool:
        """
        Connect to Finnhub WebSocket (live mode only).

        Returns:
            bool: True if connection successful
        """
        if self.mode == "historical":
            logger.warning("Cannot connect WebSocket in historical mode")
            return False

        if not self.ws_client:
            self._initialize_websocket()

        if self._connected:
            logger.info("Already connected to WebSocket")
            return True

        # Run async connection in sync context
        try:
            loop = self._get_or_create_event_loop()
            self._connected = loop.run_until_complete(self.ws_client.connect())

            if self._connected:
                logger.info("Connected to Finnhub WebSocket")
            else:
                logger.error("Failed to connect to Finnhub WebSocket")

            return self._connected

        except Exception as e:
            logger.error(f"Error connecting to WebSocket: {e}")
            return False

    def disconnect(self) -> bool:
        """
        Disconnect from Finnhub WebSocket.

        Returns:
            bool: True if disconnection successful
        """
        if not self.ws_client or not self._connected:
            logger.warning("Not connected to WebSocket")
            return True

        try:
            loop = self._get_or_create_event_loop()
            loop.run_until_complete(self.ws_client.disconnect())
            self._connected = False
            logger.info("Disconnected from Finnhub WebSocket")
            return True

        except Exception as e:
            logger.error(f"Error disconnecting from WebSocket: {e}")
            return False

    def subscribe(self, symbols: List[str]) -> bool:
        """
        Subscribe to symbols for live data.

        Args:
            symbols: List of ticker symbols

        Returns:
            bool: True if subscription successful
        """
        if self.mode == "historical":
            logger.warning("Cannot subscribe in historical mode")
            return False

        if not self._connected:
            logger.error("Not connected to WebSocket. Call connect() first.")
            return False

        try:
            loop = self._get_or_create_event_loop()
            success = loop.run_until_complete(self.ws_client.subscribe(symbols))

            if success:
                self._subscribed_symbols.update(symbols)
                logger.info(f"Subscribed to {len(symbols)} symbols")

            return success

        except Exception as e:
            logger.error(f"Error subscribing to symbols: {e}")
            return False

    def unsubscribe(self, symbols: List[str]) -> bool:
        """
        Unsubscribe from symbols.

        Args:
            symbols: List of ticker symbols

        Returns:
            bool: True if unsubscription successful
        """
        if not self._connected:
            logger.warning("Not connected to WebSocket")
            return False

        try:
            loop = self._get_or_create_event_loop()
            success = loop.run_until_complete(self.ws_client.unsubscribe(symbols))

            if success:
                self._subscribed_symbols.difference_update(symbols)
                logger.info(f"Unsubscribed from {len(symbols)} symbols")

            return success

        except Exception as e:
            logger.error(f"Error unsubscribing from symbols: {e}")
            return False

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        timeframe: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a symbol.

        Behavior by mode:
        - live: Return completed bars from aggregator
        - historical: Fetch from REST API
        - hybrid: REST for old data + aggregator for recent

        Args:
            symbol: Ticker symbol
            start: Start date (YYYY-MM-DD or datetime)
            end: End date (YYYY-MM-DD or datetime)
            timeframe: Bar interval (uses config default if None)

        Returns:
            DataFrame with OHLCV data
        """
        timeframe = timeframe or self.config.bar_interval

        if self.mode == "live":
            return self._fetch_live(symbol, start, end, timeframe)
        elif self.mode == "historical":
            return self._fetch_historical(symbol, start, end, timeframe)
        else:  # hybrid
            return self._fetch_hybrid(symbol, start, end, timeframe)

    def _fetch_live(
        self,
        symbol: str,
        start: str,
        end: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Fetch from live aggregator only."""
        if not self.bar_aggregator:
            logger.error("Bar aggregator not initialized")
            return pd.DataFrame()

        # Get completed bars from aggregator
        completed_bars = self.bar_aggregator.get_completed_bars(symbol=symbol, clear=False)

        if symbol not in completed_bars or not completed_bars[symbol]:
            logger.warning(f"No completed bars for {symbol}")
            return pd.DataFrame()

        # Convert to DataFrame
        df = self.bar_aggregator.bars_to_dataframe(completed_bars[symbol], symbol)

        # Filter by date range if needed
        if not df.empty:
            start_dt = pd.to_datetime(start)
            end_dt = pd.to_datetime(end)
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]

        logger.info(f"Fetched {len(df)} live bars for {symbol}")
        return df

    def _fetch_historical(
        self,
        symbol: str,
        start: str,
        end: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Fetch from REST API only."""
        try:
            # Convert dates to Unix timestamps
            start_dt = pd.to_datetime(start)
            end_dt = pd.to_datetime(end)

            start_ts = int(start_dt.timestamp())
            end_ts = int(end_dt.timestamp())

            # Map timeframe to Finnhub resolution
            resolution = self._map_timeframe_to_resolution(timeframe)

            # Fetch from Finnhub REST API
            logger.info(f"Fetching historical data for {symbol} from {start} to {end}")
            res = self.finnhub_client.stock_candles(
                symbol=symbol,
                resolution=resolution,
                _from=start_ts,
                to=end_ts
            )

            # Check for errors
            if res.get("s") == "no_data":
                logger.warning(f"No data available for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame({
                "open": res["o"],
                "high": res["h"],
                "low": res["l"],
                "close": res["c"],
                "volume": res["v"]
            })

            # Set timestamp index
            df["timestamp"] = pd.to_datetime(res["t"], unit="s")
            df = df.set_index("timestamp")

            # Convert to target timezone
            tz = pytz.timezone(self.config.market_hours.timezone)
            df.index = df.index.tz_localize("UTC").tz_convert(tz)

            logger.info(f"Fetched {len(df)} historical bars for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return pd.DataFrame()

    def _fetch_hybrid(
        self,
        symbol: str,
        start: str,
        end: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Fetch historical from REST + recent from aggregator."""
        try:
            # Determine split point (e.g., last 1 day from live, rest from historical)
            end_dt = pd.to_datetime(end)
            split_dt = end_dt - timedelta(days=1)
            start_dt = pd.to_datetime(start)

            # Fetch historical data up to split point
            if start_dt < split_dt:
                historical_df = self._fetch_historical(
                    symbol,
                    start,
                    split_dt.strftime("%Y-%m-%d"),
                    timeframe
                )
            else:
                historical_df = pd.DataFrame()

            # Fetch live data from split point onwards
            if self.bar_aggregator:
                live_df = self._fetch_live(
                    symbol,
                    split_dt.strftime("%Y-%m-%d"),
                    end,
                    timeframe
                )
            else:
                live_df = pd.DataFrame()

            # Combine DataFrames
            if not historical_df.empty and not live_df.empty:
                df = pd.concat([historical_df, live_df])
                df = df[~df.index.duplicated(keep='last')]  # Remove duplicates
                df = df.sort_index()
            elif not historical_df.empty:
                df = historical_df
            elif not live_df.empty:
                df = live_df
            else:
                df = pd.DataFrame()

            logger.info(f"Fetched {len(df)} hybrid bars for {symbol} "
                       f"({len(historical_df)} historical, {len(live_df)} live)")
            return df

        except Exception as e:
            logger.error(f"Error fetching hybrid data for {symbol}: {e}")
            return pd.DataFrame()

    def _map_timeframe_to_resolution(self, timeframe: str) -> str:
        """
        Map timeframe string to Finnhub resolution.

        Args:
            timeframe: Timeframe string (e.g., "1m", "5m", "1h", "1d")

        Returns:
            Finnhub resolution string (1, 5, 15, 30, 60, D, W, M)
        """
        mapping = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "1d": "D",
            "1w": "W",
            "1M": "M"
        }
        return mapping.get(timeframe, "5")

    def _get_or_create_event_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop for async operations."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        self._event_loop = loop
        return loop

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics from WebSocket and aggregator."""
        stats = {
            "mode": self.mode,
            "connected": self._connected,
            "subscribed_symbols": list(self._subscribed_symbols)
        }

        if self.ws_client:
            stats["websocket"] = self.ws_client.get_statistics()

        if self.bar_aggregator:
            stats["aggregator"] = self.bar_aggregator.get_statistics()

        return stats

    def __enter__(self):
        """Context manager entry."""
        if self.mode in ["live", "hybrid"]:
            self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self._connected:
            self.disconnect()
