"""
Unit tests for Finnhub WebSocket message parsing

Tests the FinnhubWebSocketClient._parse_message method for handling
valid/invalid trade messages, malformed JSON, and unknown message types.
"""

import pytest
import json
from datetime import datetime
from src.data.finnhub_websocket import FinnhubWebSocketClient


class TestFinnhubMessageParsing:
    """Test suite for WebSocket message parsing."""

    @pytest.fixture
    def client(self):
        """Create a WebSocket client instance."""
        return FinnhubWebSocketClient(api_key="test_key_12345")

    # ===== Valid Trade Messages =====

    def test_parse_valid_trade_message(self, client):
        """Test parsing a valid trade message with single trade."""
        message = json.dumps({
            "type": "trade",
            "data": [
                {
                    "s": "AAPL",
                    "p": 150.25,
                    "t": 1638360123456,
                    "v": 100,
                    "c": ["12"]
                }
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "trade"
        assert len(result["data"]) == 1
        assert result["data"][0]["s"] == "AAPL"
        assert result["data"][0]["p"] == 150.25
        assert result["data"][0]["v"] == 100

    def test_parse_trade_message_multiple_trades(self, client):
        """Test parsing trade message with multiple trades in one message."""
        message = json.dumps({
            "type": "trade",
            "data": [
                {"s": "AAPL", "p": 150.25, "t": 1638360123456, "v": 100},
                {"s": "AAPL", "p": 150.26, "t": 1638360123789, "v": 150},
                {"s": "AAPL", "p": 150.24, "t": 1638360124000, "v": 200}
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "trade"
        assert len(result["data"]) == 3
        assert all(trade["s"] == "AAPL" for trade in result["data"])

    def test_parse_trade_message_with_conditions(self, client):
        """Test parsing trade with trade conditions array."""
        message = json.dumps({
            "type": "trade",
            "data": [
                {
                    "s": "AAPL",
                    "p": 150.25,
                    "t": 1638360123456,
                    "v": 100,
                    "c": ["12", "37"]  # Trade conditions
                }
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["data"][0]["c"] == ["12", "37"]

    def test_parse_trade_message_no_conditions(self, client):
        """Test parsing trade without conditions field."""
        message = json.dumps({
            "type": "trade",
            "data": [
                {
                    "s": "MSFT",
                    "p": 350.50,
                    "t": 1638360123456,
                    "v": 250
                }
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        assert "c" not in result["data"][0]

    def test_parse_trade_message_empty_data(self, client):
        """Test parsing trade message with empty data array."""
        message = json.dumps({
            "type": "trade",
            "data": []
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "trade"
        assert result["data"] == []

    # ===== Subscription Messages =====

    def test_parse_subscription_confirmed(self, client):
        """Test parsing subscription confirmation message."""
        message = json.dumps({
            "type": "subscription",
            "symbol": "AAPL",
            "status": "subscribed"
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "subscription"
        assert result["symbol"] == "AAPL"
        assert result["status"] == "subscribed"

    def test_parse_subscription_unsubscribed(self, client):
        """Test parsing unsubscription confirmation message."""
        message = json.dumps({
            "type": "subscription",
            "symbol": "MSFT",
            "status": "unsubscribed"
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["status"] == "unsubscribed"

    # ===== Ping/Pong Messages =====

    def test_parse_ping_message(self, client):
        """Test parsing ping heartbeat from server."""
        message = json.dumps({"type": "ping"})

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "ping"
        assert "timestamp" in result

    # ===== Error Messages =====

    def test_parse_error_message(self, client):
        """Test parsing error message from server."""
        message = json.dumps({
            "type": "error",
            "msg": "Invalid API key"
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "error"
        assert result["msg"] == "Invalid API key"

    # ===== Malformed JSON =====

    def test_parse_invalid_json(self, client):
        """Test handling of malformed JSON."""
        message = "{invalid json content"

        result = client._parse_message(message)

        assert result is None
        assert client._stats["parse_errors"] > 0

    def test_parse_truncated_json(self, client):
        """Test handling of truncated JSON."""
        message = '{"type": "trade", "data": [{"s": "AAPL"'

        result = client._parse_message(message)

        assert result is None

    def test_parse_empty_string(self, client):
        """Test handling of empty message."""
        message = ""

        result = client._parse_message(message)

        assert result is None

    def test_parse_null_json(self, client):
        """Test handling of null JSON."""
        message = "null"

        result = client._parse_message(message)

        # null is valid JSON but won't have 'type' field
        assert result is None or result.get("type") is None

    # ===== Unknown Message Types =====

    def test_parse_unknown_message_type(self, client):
        """Test handling of unknown message type."""
        message = json.dumps({
            "type": "unknown_type",
            "data": {}
        })

        result = client._parse_message(message)

        # Should still parse but log warning
        assert result is not None
        assert result["type"] == "unknown_type"

    def test_parse_message_without_type(self, client):
        """Test handling of message without type field."""
        message = json.dumps({
            "data": {"s": "AAPL", "p": 150.25}
        })

        result = client._parse_message(message)

        # Should still parse since it's valid JSON
        assert result is not None

    # ===== Edge Cases =====

    def test_parse_message_with_extra_fields(self, client):
        """Test parsing message with unexpected extra fields."""
        message = json.dumps({
            "type": "trade",
            "data": [{"s": "AAPL", "p": 150.25, "t": 1638360123456, "v": 100}],
            "extra_field": "extra_value",
            "another_field": 12345
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["extra_field"] == "extra_value"

    def test_parse_message_with_null_fields(self, client):
        """Test parsing message with null fields."""
        message = json.dumps({
            "type": "trade",
            "data": [{"s": "AAPL", "p": 150.25, "t": 1638360123456, "v": 100}],
            "optional_field": None
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["optional_field"] is None

    def test_parse_message_unicode_symbols(self, client):
        """Test parsing message with unicode characters."""
        message = json.dumps({
            "type": "trade",
            "data": [{"s": "BRK.B", "p": 350.25, "t": 1638360123456, "v": 100}]
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["data"][0]["s"] == "BRK.B"

    def test_parse_message_large_numbers(self, client):
        """Test parsing message with very large numbers."""
        message = json.dumps({
            "type": "trade",
            "data": [
                {
                    "s": "AAPL",
                    "p": 999999.99,
                    "t": 9999999999999,
                    "v": 1000000
                }
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["data"][0]["p"] == 999999.99
        assert result["data"][0]["v"] == 1000000

    def test_parse_message_float_price_precision(self, client):
        """Test parsing of floating point prices with high precision."""
        message = json.dumps({
            "type": "trade",
            "data": [
                {
                    "s": "AAPL",
                    "p": 150.256789,
                    "t": 1638360123456,
                    "v": 100
                }
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        # Check precision is preserved
        assert abs(result["data"][0]["p"] - 150.256789) < 1e-6

    # ===== Statistics Tracking =====

    def test_parse_error_increments_counter(self, client):
        """Test that parse errors are tracked in statistics."""
        initial_errors = client._stats["parse_errors"]

        client._parse_message("{invalid}")

        assert client._stats["parse_errors"] == initial_errors + 1

    def test_multiple_parse_errors(self, client):
        """Test tracking multiple parse errors."""
        initial_errors = client._stats["parse_errors"]

        for _ in range(5):
            client._parse_message("{invalid}")

        assert client._stats["parse_errors"] == initial_errors + 5

    def test_successful_parse_does_not_increment_error_counter(self, client):
        """Test that successful parses don't increment error counter."""
        initial_errors = client._stats["parse_errors"]

        message = json.dumps({"type": "trade", "data": []})
        client._parse_message(message)

        assert client._stats["parse_errors"] == initial_errors

    # ===== Real-world Scenarios =====

    def test_parse_real_finnhub_trade_message(self, client):
        """Test parsing a real-world Finnhub trade message."""
        # This is an actual message format from Finnhub API
        message = json.dumps({
            "type": "trade",
            "data": [
                {
                    "s": "AAPL",
                    "p": 189.7,
                    "t": 1699892486821,
                    "v": 29,
                    "c": ["12"]
                },
                {
                    "s": "AAPL",
                    "p": 189.71,
                    "t": 1699892487213,
                    "v": 50,
                    "c": ["12"]
                }
            ]
        })

        result = client._parse_message(message)

        assert result is not None
        assert result["type"] == "trade"
        assert len(result["data"]) == 2

    def test_parse_rapid_fire_messages(self, client):
        """Test parsing multiple messages in rapid succession."""
        messages = [
            json.dumps({"type": "ping"}),
            json.dumps({"type": "trade", "data": [{"s": "AAPL", "p": 150.0, "t": 1000, "v": 100}]}),
            json.dumps({"type": "subscription", "symbol": "AAPL", "status": "subscribed"}),
            json.dumps({"type": "trade", "data": [{"s": "AAPL", "p": 150.1, "t": 2000, "v": 200}]}),
        ]

        results = [client._parse_message(msg) for msg in messages]

        assert all(r is not None for r in results)
        assert len([r for r in results if r and r.get("type") == "trade"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
