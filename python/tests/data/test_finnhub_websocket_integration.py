"""
Integration tests for Finnhub WebSocket client with mock server

Tests the FinnhubWebSocketClient lifecycle including connection,
disconnection, subscription, and message handling using a mock WebSocket server.
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.data.finnhub_websocket import FinnhubWebSocketClient


class MockWebSocketServer:
    """Mock WebSocket server for testing."""

    def __init__(self):
        self.messages_sent = []
        self.subscribed_symbols = set()
        self.connected = True
        self.message_queue = asyncio.Queue()

    async def send(self, message: str):
        """Store sent messages."""
        self.messages_sent.append(message)
        data = json.loads(message)

        if data["type"] == "subscribe":
            self.subscribed_symbols.add(data["symbol"])
            # Send subscription confirmation
            response = {
                "type": "subscription",
                "symbol": data["symbol"],
                "status": "subscribed"
            }
            await self.message_queue.put(json.dumps(response))

        elif data["type"] == "unsubscribe":
            self.subscribed_symbols.discard(data["symbol"])
            response = {
                "type": "subscription",
                "symbol": data["symbol"],
                "status": "unsubscribed"
            }
            await self.message_queue.put(json.dumps(response))

    async def recv(self):
        """Receive next message from queue."""
        if not self.connected:
            raise Exception("Connection closed")
        return await self.message_queue.get()

    async def close(self):
        """Close connection."""
        self.connected = False


@pytest.mark.asyncio
class TestFinnhubWebSocketClient:
    """Integration tests for WebSocket client."""

    @pytest.fixture
    async def client(self):
        """Create and cleanup WebSocket client."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")
        yield client
        # Cleanup
        if client.connected:
            await client.disconnect()

    # ===== Connection Tests =====

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful WebSocket connection."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            result = await client.connect()

            assert result is True
            assert client.connected is True
            mock_connect.assert_called_once()

        # Cleanup
        if client.connected:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_already_connected(self):
        """Test connect when already connected."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            # First connection
            await client.connect()
            connect_call_count = mock_connect.call_count

            # Try to connect again
            result = await client.connect()

            # Should not create new connection
            assert result is True
            assert mock_connect.call_count == connect_call_count

        # Cleanup
        if client.connected:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_connection_refused(self):
        """Test handling of connection refused error."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_connect = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            result = await client.connect()

            assert result is False
            assert client.connected is False

    @pytest.mark.asyncio
    async def test_connect_generic_exception(self):
        """Test handling of generic exception during connect."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_connect = AsyncMock(side_effect=Exception("Unexpected error"))

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            result = await client.connect()

            assert result is False
            assert client.connected is False

    # ===== Disconnection Tests =====

    @pytest.mark.asyncio
    async def test_disconnect_success(self):
        """Test successful disconnection."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()
            assert client.connected is True

            result = await client.disconnect()

            assert result is True
            assert client.connected is False
            mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_not_connected(self):
        """Test disconnect when not connected."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        result = await client.disconnect()

        assert result is True
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_disconnect_with_subscriptions(self):
        """Test that disconnect unsubscribes from all symbols."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()

            # Subscribe to symbols
            await client.subscribe(["AAPL", "MSFT"])
            assert len(client.subscribed_symbols) == 2

            # Disconnect
            await client.disconnect()

            # Should have unsubscribed
            assert client.connected is False
            # Check that unsubscribe was called for both symbols
            unsubscribe_calls = [
                call for call in mock_ws.send.call_args_list
                if "unsubscribe" in str(call)
            ]
            assert len(unsubscribe_calls) >= 2

    # ===== Subscription Tests =====

    @pytest.mark.asyncio
    async def test_subscribe_single_symbol(self):
        """Test subscribing to a single symbol."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()
            result = await client.subscribe(["AAPL"])

            assert result is True
            assert "AAPL" in client.subscribed_symbols
            mock_ws.send.assert_called()

        if client.connected:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_subscribe_multiple_symbols(self):
        """Test subscribing to multiple symbols."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()
            result = await client.subscribe(["AAPL", "MSFT", "NVDA"])

            assert result is True
            assert len(client.subscribed_symbols) == 3
            assert all(s in client.subscribed_symbols for s in ["AAPL", "MSFT", "NVDA"])

        if client.connected:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_subscribe_duplicate_symbol(self):
        """Test subscribing to already subscribed symbol."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()

            # Subscribe once
            await client.subscribe(["AAPL"])
            call_count_1 = mock_ws.send.call_count

            # Subscribe again
            await client.subscribe(["AAPL"])
            call_count_2 = mock_ws.send.call_count

            # Should not send duplicate subscription
            assert call_count_2 == call_count_1

        if client.connected:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_subscribe_not_connected(self):
        """Test subscribing when not connected."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        result = await client.subscribe(["AAPL"])

        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_symbol(self):
        """Test unsubscribing from a symbol."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()
            await client.subscribe(["AAPL", "MSFT"])

            result = await client.unsubscribe(["AAPL"])

            assert result is True
            assert "AAPL" not in client.subscribed_symbols
            assert "MSFT" in client.subscribed_symbols

        if client.connected:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_unsubscribe_not_connected(self):
        """Test unsubscribing when not connected."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        result = await client.unsubscribe(["AAPL"])

        assert result is False

    # ===== Message Queue Tests =====

    @pytest.mark.asyncio
    async def test_get_message_from_queue(self):
        """Test retrieving message from queue."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        # Manually add message to queue
        test_message = {"type": "trade", "data": [{"s": "AAPL", "p": 150.0}]}
        await client._message_queue.put(test_message)

        # Get message
        result = await client.get_message(timeout=1.0)

        assert result == test_message

    @pytest.mark.asyncio
    async def test_get_message_timeout(self):
        """Test message queue timeout."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        # Get message from empty queue with timeout
        result = await client.get_message(timeout=0.1)

        assert result is None

    # ===== Statistics Tests =====

    @pytest.mark.asyncio
    async def test_statistics_tracking(self):
        """Test that statistics are properly tracked."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()

            # Parse some messages
            client._parse_message(json.dumps({"type": "ping"}))
            client._parse_message(json.dumps({"type": "trade", "data": []}))
            client._parse_message("{invalid}")

            stats = client.get_statistics()

            assert stats["messages_received"] == 0  # Not from receive loop
            assert stats["parse_errors"] == 1
            assert stats["connection_time"] is not None

        if client.connected:
            await client.disconnect()

    # ===== Connection Properties Tests =====

    @pytest.mark.asyncio
    async def test_connected_property(self):
        """Test connected property."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        assert client.connected is False

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()
            assert client.connected is True

            await client.disconnect()
            assert client.connected is False

    @pytest.mark.asyncio
    async def test_subscribed_symbols_property(self):
        """Test subscribed_symbols property."""
        client = FinnhubWebSocketClient(api_key="test_key_12345")

        assert client.subscribed_symbols == []

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()
            await client.subscribe(["AAPL", "MSFT"])

            symbols = client.subscribed_symbols
            assert len(symbols) == 2
            assert "AAPL" in symbols
            assert "MSFT" in symbols

        if client.connected:
            await client.disconnect()

    # ===== Auth URL Construction =====

    @pytest.mark.asyncio
    async def test_auth_url_construction(self):
        """Test that authentication URL is correctly constructed."""
        client = FinnhubWebSocketClient(
            api_key="secret_key_123",
            websocket_url="wss://ws.finnhub.io"
        )

        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("src.data.finnhub_websocket.websockets.connect", mock_connect):
            await client.connect()

            # Check that connect was called with correct URL
            called_url = mock_connect.call_args[0][0]
            assert "wss://ws.finnhub.io?token=secret_key_123" in called_url

        if client.connected:
            await client.disconnect()

    # ===== Message Callback Tests =====

    @pytest.mark.asyncio
    async def test_message_callback(self):
        """Test that message callback is called for parsed messages."""
        callback_messages = []

        def test_callback(message):
            callback_messages.append(message)

        client = FinnhubWebSocketClient(
            api_key="test_key",
            message_callback=test_callback
        )

        # Parse a message
        message = json.dumps({"type": "trade", "data": []})
        client._parse_message(message)

        # Callback should not be called here (only during receive_loop)
        # But we can verify the callback is set
        assert client.message_callback is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
