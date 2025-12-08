"""
Finnhub WebSocket Client

Handles WebSocket connection, authentication, subscription management,
and message parsing for Finnhub real-time trade data.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from src.utils.logger import get_logger

logger = get_logger("FinnhubWebSocket")


class FinnhubWebSocketClient:
    """
    Async WebSocket client for Finnhub real-time trade data.

    Manages connection lifecycle, subscriptions, and message parsing.
    Uses asyncio.Queue for thread-safe message passing to consumers.
    """

    def __init__(
        self,
        api_key: str,
        websocket_url: str = "wss://ws.finnhub.io",
        message_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        ping_interval: int = 20,
        ping_timeout: int = 10
    ):
        """
        Initialize Finnhub WebSocket client.

        Args:
            api_key: Finnhub API key for authentication
            websocket_url: WebSocket endpoint URL
            message_callback: Optional callback function for incoming messages
            ping_interval: Seconds between ping messages (keep-alive)
            ping_timeout: Seconds to wait for pong response
        """
        self.api_key = api_key
        self.websocket_url = websocket_url
        self.message_callback = message_callback
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        # Connection state
        self._websocket = None
        self._connected = False
        self._running = False
        self._receive_task = None
        self._ping_task = None

        # Subscription tracking
        self._subscribed_symbols: set = set()

        # Message queue for consumer threads
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # Statistics
        self._stats = {
            "messages_received": 0,
            "messages_parsed": 0,
            "parse_errors": 0,
            "connection_time": None,
            "last_message_time": None
        }

    @property
    def connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected and self._websocket is not None

    @property
    def subscribed_symbols(self) -> List[str]:
        """Get list of currently subscribed symbols."""
        return list(self._subscribed_symbols)

    async def connect(self) -> bool:
        """
        Establish WebSocket connection and authenticate.

        Returns:
            bool: True if connection successful, False otherwise
        """
        if self.connected:
            logger.warning("Already connected to Finnhub WebSocket")
            return True

        try:
            # Construct authenticated URL
            auth_url = f"{self.websocket_url}?token={self.api_key}"

            logger.info(f"Connecting to Finnhub WebSocket: {self.websocket_url}")

            # Establish WebSocket connection with ping settings
            self._websocket = await websockets.connect(
                auth_url,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout
            )

            self._connected = True
            self._running = True
            self._stats["connection_time"] = datetime.now()

            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())

            logger.info("[SUCCESS] Successfully connected to Finnhub WebSocket")
            return True

        except ConnectionRefusedError as e:
            logger.error(f"Connection refused: {e}")
            self._connected = False
            return False
        except WebSocketException as e:
            logger.error(f"WebSocket error during connection: {e}")
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to WebSocket: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> bool:
        """
        Close WebSocket connection cleanly.

        Returns:
            bool: True if disconnection successful
        """
        if not self.connected:
            logger.warning("Not connected to Finnhub WebSocket")
            return True

        try:
            logger.info("Disconnecting from Finnhub WebSocket...")

            # Stop background tasks
            self._running = False

            # Unsubscribe from all symbols
            if self._subscribed_symbols:
                symbols_to_unsub = list(self._subscribed_symbols)
                await self.unsubscribe(symbols_to_unsub)

            # Cancel receive task
            if self._receive_task and not self._receive_task.done():
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass

            # Close WebSocket
            if self._websocket:
                await self._websocket.close()
                self._websocket = None

            self._connected = False
            logger.info("[SUCCESS] Disconnected from Finnhub WebSocket")
            return True

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")
            self._connected = False
            return False

    async def subscribe(self, symbols: List[str]) -> bool:
        """
        Subscribe to real-time trades for given symbols.

        Args:
            symbols: List of ticker symbols to subscribe to

        Returns:
            bool: True if subscription successful
        """
        if not self.connected:
            logger.error("Cannot subscribe: not connected to WebSocket")
            return False

        try:
            for symbol in symbols:
                if symbol in self._subscribed_symbols:
                    logger.debug(f"Already subscribed to {symbol}, skipping")
                    continue

                subscribe_msg = {
                    "type": "subscribe",
                    "symbol": symbol
                }

                await self._websocket.send(json.dumps(subscribe_msg))
                self._subscribed_symbols.add(symbol)
                logger.info(f"> Subscribed to {symbol}")

            return True

        except ConnectionClosed as e:
            logger.error(f"Connection closed during subscribe: {e}")
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Error subscribing to symbols: {e}")
            return False

    async def unsubscribe(self, symbols: List[str]) -> bool:
        """
        Unsubscribe from real-time trades for given symbols.

        Args:
            symbols: List of ticker symbols to unsubscribe from

        Returns:
            bool: True if unsubscription successful
        """
        if not self.connected:
            logger.warning("Cannot unsubscribe: not connected to WebSocket")
            return False

        try:
            for symbol in symbols:
                if symbol not in self._subscribed_symbols:
                    logger.debug(f"Not subscribed to {symbol}, skipping")
                    continue

                unsubscribe_msg = {
                    "type": "unsubscribe",
                    "symbol": symbol
                }

                await self._websocket.send(json.dumps(unsubscribe_msg))
                self._subscribed_symbols.discard(symbol)
                logger.info(f"[SUCCESS] Unsubscribed from {symbol}")

            return True

        except ConnectionClosed as e:
            logger.error(f"Connection closed during unsubscribe: {e}")
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Error unsubscribing from symbols: {e}")
            return False

    async def _receive_loop(self):
        """
        Background task to receive and process WebSocket messages.
        Runs until connection is closed or _running flag is set to False.
        """
        logger.debug("Message receive loop started")

        try:
            while self._running and self.connected:
                try:
                    # Receive message with timeout
                    message = await asyncio.wait_for(
                        self._websocket.recv(),
                        timeout=60.0  # 60 second timeout
                    )

                    self._stats["messages_received"] += 1
                    self._stats["last_message_time"] = datetime.now()

                    # Parse and handle message
                    parsed_msg = self._parse_message(message)

                    if parsed_msg:
                        self._stats["messages_parsed"] += 1

                        # Add to queue for consumers
                        try:
                            self._message_queue.put_nowait(parsed_msg)
                        except asyncio.QueueFull:
                            logger.warning("Message queue full, dropping message")

                        # Call callback if provided
                        if self.message_callback:
                            try:
                                self.message_callback(parsed_msg)
                            except Exception as e:
                                logger.error(f"Error in message callback: {e}")

                except asyncio.TimeoutError:
                    logger.warning("No message received in 60 seconds")
                    # Check if connection is still alive
                    if not self.connected:
                        logger.error("Connection appears dead, exiting receive loop")
                        break
                    continue

                except ConnectionClosed as e:
                    logger.warning(f"WebSocket connection closed: {e}")
                    self._connected = False
                    break

        except asyncio.CancelledError:
            logger.debug("Receive loop cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in receive loop: {e}")
            self._connected = False
        finally:
            logger.debug("Message receive loop stopped")

    def _parse_message(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Parse incoming WebSocket message.

        Args:
            message: Raw message string from WebSocket

        Returns:
            Parsed message dict or None if parsing fails
        """
        try:
            data = json.loads(message)

            # Handle different message types
            msg_type = data.get("type")

            if msg_type == "ping":
                # Heartbeat from server (handled automatically by websockets library)
                logger.debug("Received ping from server")
                return {"type": "ping", "timestamp": datetime.now()}

            elif msg_type == "trade":
                # Trade data message
                trades = data.get("data", [])
                if trades:
                    logger.debug(f"Received {len(trades)} trades")
                return data

            elif msg_type == "subscription":
                # Subscription confirmation
                symbol = data.get("symbol")
                status = data.get("status")
                logger.debug(f"Subscription update: {symbol} - {status}")
                return data

            elif msg_type == "error":
                # Error message
                error_msg = data.get("msg", "Unknown error")
                logger.error(f"Finnhub error: {error_msg}")
                return data

            else:
                # Unknown message type
                logger.warning(f"Unknown message type: {msg_type}")
                return data

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON message: {e}")
            logger.debug(f"Raw message: {message[:200]}")
            self._stats["parse_errors"] += 1
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing message: {e}")
            self._stats["parse_errors"] += 1
            return None

    async def get_message(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Get next message from queue (for consumer threads).

        Args:
            timeout: Optional timeout in seconds

        Returns:
            Parsed message dict or None if timeout
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=timeout
                )
            else:
                return await self._message_queue.get()
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error getting message from queue: {e}")
            return None

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get client statistics.

        Returns:
            Dictionary with connection stats
        """
        stats = self._stats.copy()
        stats.update({
            "connected": self.connected,
            "subscribed_symbols": list(self._subscribed_symbols),
            "queue_size": self._message_queue.qsize()
        })
        return stats

    def get_connection_status(self) -> Dict[str, Any]:
        """
        Get current connection status.

        Returns:
            Dictionary with connection details
        """
        return {
            "connected": self.connected,
            "websocket_url": self.websocket_url,
            "subscribed_symbols": list(self._subscribed_symbols),
            "connection_time": self._stats["connection_time"],
            "last_message_time": self._stats["last_message_time"],
            "uptime_seconds": (
                (datetime.now() - self._stats["connection_time"]).total_seconds()
                if self._stats["connection_time"] else 0
            )
        }


# Example usage and testing
if __name__ == "__main__":
    async def example_callback(message: Dict[str, Any]):
        """Example message callback."""
        msg_type = message.get("type")
        if msg_type == "trade":
            trades = message.get("data", [])
            for trade in trades[:3]:  # Print first 3 trades
                print(f"  {trade.get('s')}: ${trade.get('p')} x {trade.get('v')} @ {trade.get('t')}")

    async def main():
        # Load config (in real usage, import from config_loader)
        print("Note: Replace 'your_api_key' with actual Finnhub API key")

        client = FinnhubWebSocketClient(
            api_key="your_api_key",
            message_callback=example_callback
        )

        # Connect
        connected = await client.connect()
        if not connected:
            print("Failed to connect")
            return

        # Subscribe to symbols
        await client.subscribe(["AAPL", "MSFT"])

        # Run for 30 seconds
        print("Listening for 30 seconds...")
        await asyncio.sleep(30)

        # Print stats
        print("\nStatistics:")
        stats = client.get_statistics()
        for key, value in stats.items():
            print(f"  {key}: {value}")

        # Disconnect
        await client.disconnect()

    # Run example
    asyncio.run(main())
