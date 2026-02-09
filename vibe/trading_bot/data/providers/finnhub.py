"""
Finnhub WebSocket client for real-time trade streaming.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, Set

import websockets
from websockets.client import WebSocketClientProtocol

logger = logging.getLogger(__name__)


class ConnectionState(str, Enum):
    """WebSocket connection state machine."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class FinnhubWebSocketClient:
    """
    Real-time trade streaming client for Finnhub WebSocket API.

    Provides automatic reconnection with exponential backoff and gap detection.
    """

    BASE_URL = "wss://ws.finnhub.io"
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_BACKOFF = [1, 2, 4, 8, 16]  # Exponential backoff in seconds
    GAP_DETECTION_THRESHOLD = 60  # Gap > 60s triggers backfill request

    def __init__(self, api_key: str):
        """
        Initialize Finnhub WebSocket client.

        Args:
            api_key: Finnhub API key
        """
        self.api_key = api_key
        self.state = ConnectionState.DISCONNECTED
        self.connected = False
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

        # Task management
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

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
                self.connected = True
                self.reconnect_attempts = 0
                self.last_message_time = datetime.now()

                logger.info("Connected to Finnhub WebSocket")

                if self._on_connected:
                    await self._on_connected()

                # Start listen task
                self._listen_task = asyncio.create_task(self._listen_messages())

                # Start heartbeat task for gap detection
                self._heartbeat_task = asyncio.create_task(self._heartbeat_check())

                return

            except Exception as e:
                self.reconnect_attempts += 1
                backoff_time = self.RECONNECT_BACKOFF[
                    min(self.reconnect_attempts - 1, len(self.RECONNECT_BACKOFF) - 1)
                ]

                logger.warning(
                    f"Connection attempt {self.reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS} "
                    f"failed, retrying in {backoff_time}s: {str(e)}"
                )

                if self.reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                    raise

                await asyncio.sleep(backoff_time)

    async def disconnect(self) -> None:
        """Disconnect from WebSocket gracefully."""
        self.connected = False
        self.state = ConnectionState.DISCONNECTED

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
        """Listen for messages from WebSocket."""
        try:
            while self.connected and self.ws:
                try:
                    message = await asyncio.wait_for(self.ws.recv(), timeout=30.0)
                    self.last_message_time = datetime.now()

                    await self._handle_message(json.loads(message))

                except asyncio.TimeoutError:
                    logger.warning("WebSocket receive timeout")
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

        Args:
            data: Parsed JSON message
        """
        # Handle trade data
        if "data" in data:
            trades = data["data"]
            if isinstance(trades, list):
                for trade in trades:
                    if self._on_trade:
                        await self._on_trade(
                            {
                                "symbol": data.get("s"),
                                "price": trade.get("p"),
                                "size": trade.get("s"),
                                "timestamp": datetime.fromtimestamp(
                                    trade.get("t", 0) / 1000
                                ),
                                "bid": trade.get("bp"),
                                "ask": trade.get("ap"),
                            }
                        )

        # Handle ping/pong
        elif data.get("type") == "ping":
            if self.ws:
                await self.ws.send(json.dumps({"type": "pong"}))

    async def _heartbeat_check(self) -> None:
        """
        Check for message gaps and trigger backfill if needed.

        Detects if reconnect takes > 1 minute and triggers backfill request.
        """
        try:
            while self.connected:
                await asyncio.sleep(10)  # Check every 10 seconds

                if self.last_message_time is None:
                    continue

                time_since_last_message = (
                    datetime.now() - self.last_message_time
                ).total_seconds()

                if time_since_last_message > self.GAP_DETECTION_THRESHOLD:
                    logger.warning(
                        f"Data gap detected: {time_since_last_message:.0f}s "
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

        except asyncio.CancelledError:
            logger.debug("Heartbeat task cancelled")
        except Exception as e:
            logger.error(f"Error in heartbeat check: {str(e)}")

    async def _handle_disconnect(self) -> None:
        """Handle unexpected disconnect and attempt reconnection."""
        if not self.connected:
            return

        logger.warning("WebSocket disconnected, attempting reconnection")
        self.connected = False
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
