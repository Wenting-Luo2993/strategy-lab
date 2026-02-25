"""
Finnhub WebSocket client for real-time trade streaming.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Set

import pandas as pd
import pytz
import websockets
from websockets.client import WebSocketClientProtocol

from .types import WebSocketDataProvider, ProviderType
from vibe.common.models import Bar

logger = logging.getLogger(__name__)


class ConnectionState(str, Enum):
    """WebSocket connection state machine."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class FinnhubWebSocketClient(WebSocketDataProvider):
    """
    Real-time trade streaming client for Finnhub WebSocket API.

    Provides automatic reconnection with exponential backoff and gap detection.
    """

    BASE_URL = "wss://ws.finnhub.io"
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_BACKOFF = [1, 2, 4, 8, 16]  # Exponential backoff in seconds
    RATE_LIMIT_BACKOFF = 60  # Wait 60 seconds (1 minute) when rate limited
    GAP_DETECTION_THRESHOLD = 60  # Gap > 60s triggers backfill request

    def __init__(self, api_key: str):
        """
        Initialize Finnhub WebSocket client.

        Args:
            api_key: Finnhub API key
        """
        self.api_key = api_key
        self.state = ConnectionState.DISCONNECTED
        self._connected = False
        self.ws: Optional[WebSocketClientProtocol] = None

        # Subscribed symbols
        self.subscribed_symbols: Set[str] = set()

        # Event handlers
        self._on_connected: Optional[Callable] = None
        self._on_disconnected: Optional[Callable] = None
        self._on_trade: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

        # Reconnection state
        self.reconnect_attempts = 0
        self.last_message_time: Optional[datetime] = None
        self.last_ping_time: Optional[datetime] = None
        self.last_pong_time: Optional[datetime] = None

        # Task management
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        # Tick logging for validation (controlled by environment variable)
        self._log_ticks = os.getenv("LOG_FINNHUB_TICKS", "").lower() in ("true", "1", "yes")
        self._tick_log_file = None
        if self._log_ticks:
            tick_log_dir = Path(os.getenv("TICK_LOG_DIR", "./data/tick_logs"))
            tick_log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._tick_log_file = tick_log_dir / f"finnhub_ticks_{timestamp}.jsonl"
            logger.info(f"Tick logging ENABLED → {self._tick_log_file}")
        else:
            logger.info("Tick logging disabled (set LOG_FINNHUB_TICKS=true to enable)")

    # WebSocketDataProvider interface implementation
    @property
    def provider_type(self) -> ProviderType:
        """WebSocket provider."""
        return ProviderType.WEBSOCKET

    @property
    def provider_name(self) -> str:
        """Provider name."""
        return "Finnhub"

    @property
    def is_real_time(self) -> bool:
        """Finnhub provides real-time data."""
        return True

    @property
    def connected(self) -> bool:
        """Is the WebSocket currently connected?"""
        return self._connected

    async def get_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        days: int
    ) -> pd.DataFrame:
        """Finnhub WebSocket doesn't provide historical data."""
        logger.warning(
            f"Finnhub WebSocket doesn't support historical data. "
            f"Use Yahoo Finance or Polygon for historical bars."
        )
        return pd.DataFrame()

    # Common DataProvider interface (from vibe.common.data.base)
    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """Not supported for WebSocket provider."""
        return await self.get_historical_bars(symbol, timeframe, 1)

    async def get_current_price(self, symbol: str) -> float:
        """Not directly supported - use last trade from callback."""
        logger.warning("get_current_price not supported for WebSocket provider")
        return 0.0

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        """Not directly supported - bars come through callbacks."""
        logger.warning("get_bar not supported for WebSocket provider")
        return None

    def on_connected(self, callback: Callable) -> None:
        """Register callback for connection established."""
        self._on_connected = callback

    def on_disconnected(self, callback: Callable) -> None:
        """Register callback for connection lost."""
        self._on_disconnected = callback

    def on_trade(self, callback: Callable) -> None:
        """Register callback for trade events."""
        self._on_trade = callback

    def on_error(self, callback: Callable) -> None:
        """Register callback for errors."""
        self._on_error = callback

    async def connect(self) -> None:
        """
        Connect to Finnhub WebSocket and authenticate.

        Raises:
            Exception: If connection fails after max attempts
        """
        if self.connected:
            logger.warning("Already connected")
            return

        self.state = ConnectionState.CONNECTING
        self.reconnect_attempts = 0

        try:
            await self._connect_with_retry()
        except Exception as e:
            self.state = ConnectionState.DISCONNECTED
            logger.error(f"Failed to connect to Finnhub: {str(e)}")
            if self._on_error:
                await self._on_error({"message": str(e), "type": "connection_error"})
            raise

    async def _connect_with_retry(self) -> None:
        """Connect with exponential backoff retry."""
        while self.reconnect_attempts < self.MAX_RECONNECT_ATTEMPTS:
            try:
                url = f"{self.BASE_URL}?token={self.api_key}"
                self.ws = await websockets.connect(url)
                self.state = ConnectionState.CONNECTED
                self._connected = True
                self.reconnect_attempts = 0

                # Reset all timestamp trackers for fresh connection
                self.last_message_time = datetime.now()
                self.last_ping_time = None
                self.last_pong_time = None

                logger.info("Connected to Finnhub WebSocket")

                if self._on_connected:
                    await self._on_connected()

                # Start listen task
                self._listen_task = asyncio.create_task(self._listen_messages())

                # Start heartbeat task for gap detection
                self._heartbeat_task = asyncio.create_task(self._heartbeat_check())

                return

            except Exception as e:
                error_str = str(e)

                # Detect rate limiting (HTTP 429)
                if "429" in error_str or "rate limit" in error_str.lower():
                    logger.error(
                        f"⚠️  RATE LIMITED (HTTP 429) - Finnhub free tier quota exceeded"
                    )
                    logger.error(
                        f"   Will retry in {self.RATE_LIMIT_BACKOFF}s (1 minute)"
                    )
                    logger.error(
                        f"   During this time, NO real-time data will be received"
                    )
                    logger.error(
                        f"   Possible causes:"
                    )
                    logger.error(
                        f"   - Multiple bot instances running (check for duplicates)"
                    )
                    logger.error(
                        f"   - Too many connection attempts (rapid reconnects)"
                    )
                    logger.error(
                        f"   - Free tier limit: 60 requests/minute, 1 websocket connection"
                    )

                    # Wait 1 minute before retrying on rate limit
                    await asyncio.sleep(self.RATE_LIMIT_BACKOFF)

                    # Reset attempts so we keep trying after rate limit expires
                    self.reconnect_attempts = 0
                    continue

                # Normal errors - use exponential backoff
                self.reconnect_attempts += 1
                backoff_time = self.RECONNECT_BACKOFF[
                    min(self.reconnect_attempts - 1, len(self.RECONNECT_BACKOFF) - 1)
                ]

                logger.warning(
                    f"Connection attempt {self.reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS} "
                    f"failed, retrying in {backoff_time}s: {error_str}"
                )

                if self.reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                    raise

                await asyncio.sleep(backoff_time)

    async def disconnect(self) -> None:
        """Disconnect from WebSocket gracefully."""
        self._connected = False
        self.state = ConnectionState.DISCONNECTED

        # Reset timestamp trackers
        self.last_message_time = None
        self.last_ping_time = None
        self.last_pong_time = None

        # Cancel all tasks and wait for them to finish
        tasks_to_cancel = []
        if self._listen_task:
            self._listen_task.cancel()
            tasks_to_cancel.append(self._listen_task)
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            tasks_to_cancel.append(self._heartbeat_task)
        if self._reconnect_task:
            self._reconnect_task.cancel()
            tasks_to_cancel.append(self._reconnect_task)

        # Wait for all cancelled tasks to complete
        for task in tasks_to_cancel:
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected for cancelled tasks
            except Exception as e:
                logger.error(f"Error while cancelling task: {e}")

        # Close WebSocket
        if self.ws:
            await self.ws.close()
            self.ws = None

        logger.info("Disconnected from Finnhub WebSocket")

        if self._on_disconnected:
            await self._on_disconnected()

    async def subscribe(self, symbol: str) -> None:
        """
        Subscribe to trades for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'AAPL')
        """
        if not self.connected:
            raise RuntimeError("Not connected to WebSocket")

        if symbol in self.subscribed_symbols:
            logger.debug(f"Already subscribed to {symbol}")
            return

        message = {"type": "subscribe", "symbol": symbol}

        try:
            await self.ws.send(json.dumps(message))
            self.subscribed_symbols.add(symbol)
            logger.info(f"Subscribed to {symbol}")
        except Exception as e:
            logger.error(f"Error subscribing to {symbol}: {str(e)}")
            if self._on_error:
                await self._on_error(
                    {"message": str(e), "type": "subscription_error", "symbol": symbol}
                )
            raise

    async def unsubscribe(self, symbol: str) -> None:
        """
        Unsubscribe from trades for a symbol.

        Args:
            symbol: Trading symbol
        """
        if not self.connected:
            logger.warning("Not connected to WebSocket")
            return

        if symbol not in self.subscribed_symbols:
            logger.debug(f"Not subscribed to {symbol}")
            return

        message = {"type": "unsubscribe", "symbol": symbol}

        try:
            await self.ws.send(json.dumps(message))
            self.subscribed_symbols.discard(symbol)
            logger.info(f"Unsubscribed from {symbol}")
        except Exception as e:
            logger.error(f"Error unsubscribing from {symbol}: {str(e)}")

    async def _listen_messages(self) -> None:
        """
        Listen for messages from WebSocket.

        CRITICAL: Ping/pong handling is done with highest priority to prevent
        disconnections due to missed pongs. Finnhub has strict ping/pong timeout.

        Timeout is set to 90s to accommodate Finnhub's ~60s ping interval. During
        warm-up and low-activity periods, we may not receive trade messages, so we
        rely on pings to keep the connection alive.
        """
        try:
            while self.connected and self.ws:
                try:
                    # Timeout: 90s allows for Finnhub's 60s ping interval + buffer
                    # Previous 30s timeout was too aggressive and caused reconnect loops
                    message = await asyncio.wait_for(self.ws.recv(), timeout=90.0)
                    self.last_message_time = datetime.now()

                    # Parse message
                    data = json.loads(message)

                    # CRITICAL: Handle ping with HIGHEST PRIORITY
                    # Respond immediately before any other processing
                    if data.get("type") == "ping":
                        self.last_ping_time = datetime.now()
                        try:
                            # Send pong immediately - don't await other operations
                            await self.ws.send(json.dumps({"type": "pong"}))
                            self.last_pong_time = datetime.now()
                            logger.debug("Responded to ping from Finnhub")
                        except Exception as e:
                            logger.error(f"Failed to send pong: {e}")
                            # Pong failure is critical - reconnect
                            await self._handle_disconnect()
                            break
                        continue  # Skip other processing, go to next message

                    # Process other messages (trades, etc.)
                    await self._handle_message(data)

                except asyncio.TimeoutError:
                    logger.warning("WebSocket receive timeout (no messages for 90s)")
                    await self._handle_disconnect()
                    break

                except Exception as e:
                    logger.error(f"Error receiving message: {str(e)}")
                    if self._on_error:
                        await self._on_error(
                            {"message": str(e), "type": "message_error"}
                        )
                    break

        except asyncio.CancelledError:
            logger.debug("Listen task cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in listen loop: {str(e)}")

        await self._handle_disconnect()

    async def _handle_message(self, data: dict) -> None:
        """
        Handle received message from WebSocket.

        Note: Ping/pong is now handled in _listen_messages() with highest priority.

        Args:
            data: Parsed JSON message
        """
        # Handle trade data
        if "data" in data:
            trades = data["data"]
            if isinstance(trades, list):
                for trade in trades:
                    # Log raw tick to file if enabled
                    if self._log_ticks and self._tick_log_file:
                        try:
                            tick_record = {
                                "received_at": datetime.now(pytz.UTC).isoformat(),
                                "symbol": trade.get("s"),
                                "price": trade.get("p"),
                                "volume": trade.get("v"),
                                "timestamp_ms": trade.get("t"),
                                "timestamp": datetime.fromtimestamp(
                                    trade.get("t", 0) / 1000,
                                    tz=pytz.UTC
                                ).isoformat(),
                                "conditions": trade.get("c", []),
                                "bid_price": trade.get("bp"),
                                "ask_price": trade.get("ap"),
                                "bid_size": trade.get("bs"),
                                "ask_size": trade.get("as"),
                            }
                            with open(self._tick_log_file, "a") as f:
                                f.write(json.dumps(tick_record) + "\n")
                        except Exception as e:
                            logger.error(f"Failed to log tick: {e}")

                    # Process trade callback
                    if self._on_trade:
                        await self._on_trade(
                            {
                                "symbol": trade.get("s"),  # Symbol is in each trade
                                "price": trade.get("p"),
                                "size": trade.get("v"),    # Volume is "v", not "s"
                                "timestamp": datetime.fromtimestamp(
                                    trade.get("t", 0) / 1000,
                                    tz=pytz.UTC  # Finnhub timestamps are in UTC
                                ),
                                "bid": trade.get("bp"),
                                "ask": trade.get("ap"),
                            }
                        )

    async def _heartbeat_check(self) -> None:
        """
        Enhanced heartbeat monitoring for Finnhub WebSocket.

        Monitors:
        1. Message gaps (> 60s since last message)
        2. Ping health (> 45s since last ping = connection issue)
        3. Pong response time (track if we're responding quickly)

        Finnhub has strict ping/pong timeout, so we monitor ping frequency
        to detect connection health issues proactively.
        """
        try:
            while self.connected:
                await asyncio.sleep(10)  # Check every 10 seconds

                now = datetime.now()

                # Check 1: Monitor last message time (any message)
                if self.last_message_time is not None:
                    time_since_last_message = (now - self.last_message_time).total_seconds()

                    if time_since_last_message > self.GAP_DETECTION_THRESHOLD:
                        logger.warning(
                            f"⚠️  Data gap detected: {time_since_last_message:.0f}s "
                            f"since last message"
                        )

                        if self._on_error:
                            await self._on_error(
                                {
                                    "message": f"Data gap: {time_since_last_message:.0f}s",
                                    "type": "gap_detected",
                                    "gap_duration": time_since_last_message,
                                }
                            )

                        # Trigger reconnection
                        await self._handle_disconnect()
                        break

                # Check 2: Monitor ping health
                # Finnhub typically sends pings every 60 seconds
                # If no ping for > 90s, connection might be stale
                if self.last_ping_time is not None:
                    time_since_last_ping = (now - self.last_ping_time).total_seconds()

                    if time_since_last_ping > 90:
                        logger.warning(
                            f"⚠️  No ping from Finnhub for {time_since_last_ping:.0f}s "
                            f"(expected every ~60s). Connection may be stale."
                        )

                        # Don't trigger immediate reconnect, but log it
                        # The message timeout will catch true disconnects

                # Check 3: Log pong response time health
                if self.last_ping_time and self.last_pong_time:
                    if self.last_pong_time >= self.last_ping_time:
                        response_time = (self.last_pong_time - self.last_ping_time).total_seconds()
                        if response_time > 1.0:
                            logger.warning(
                                f"⚠️  Slow pong response: {response_time:.2f}s "
                                f"(should be < 1s for Finnhub)"
                            )

        except asyncio.CancelledError:
            logger.debug("Heartbeat task cancelled")
        except Exception as e:
            logger.error(f"Error in heartbeat check: {str(e)}")

    async def _handle_disconnect(self) -> None:
        """Handle unexpected disconnect and attempt reconnection."""
        if not self.connected:
            return

        logger.warning("WebSocket disconnected, attempting reconnection")
        self._connected = False
        self.state = ConnectionState.RECONNECTING

        if self._on_disconnected:
            await self._on_disconnected()

        # Attempt reconnection with exponential backoff
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect to WebSocket."""
        try:
            while self.reconnect_attempts < self.MAX_RECONNECT_ATTEMPTS:
                backoff_time = self.RECONNECT_BACKOFF[
                    min(self.reconnect_attempts, len(self.RECONNECT_BACKOFF) - 1)
                ]

                logger.info(
                    f"Reconnecting in {backoff_time}s "
                    f"(attempt {self.reconnect_attempts + 1}/{self.MAX_RECONNECT_ATTEMPTS})"
                )

                await asyncio.sleep(backoff_time)

                try:
                    await self._connect_with_retry()

                    # Re-subscribe to symbols
                    for symbol in list(self.subscribed_symbols):
                        await self.subscribe(symbol)

                    logger.info("Successfully reconnected and re-subscribed")
                    return

                except Exception as e:
                    self.reconnect_attempts += 1
                    logger.warning(f"Reconnection attempt {self.reconnect_attempts} failed: {str(e)}")

            logger.error(
                f"Failed to reconnect after {self.MAX_RECONNECT_ATTEMPTS} attempts"
            )

        except asyncio.CancelledError:
            logger.debug("Reconnect loop cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in reconnect loop: {str(e)}")
