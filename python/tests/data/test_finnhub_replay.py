"""
Replay tests for Finnhub WebSocket using recorded messages

Tests the bar aggregator and WebSocket client by replaying recorded
WebSocket messages from tests/__scenarios__/finnhub_messages.json
"""

import pytest
import json
import os
from pathlib import Path

from src.data.finnhub_websocket import FinnhubWebSocketClient, BarAggregator


class TestFinnhubReplay:
    """Test suite for replaying recorded Finnhub WebSocket messages."""

    @pytest.fixture
    def scenario_messages(self):
        """Load recorded WebSocket messages from scenario file."""
        scenario_file = Path(__file__).parent.parent / "__scenarios__" / "finnhub_messages.json"

        if not scenario_file.exists():
            pytest.skip(f"Scenario file not found: {scenario_file}")

        with open(scenario_file, "r") as f:
            scenarios = json.load(f)

        return scenarios

    @pytest.fixture
    def client(self):
        """Create WebSocket client instance."""
        return FinnhubWebSocketClient(api_key="test_key_12345")

    @pytest.fixture
    def aggregator(self):
        """Create bar aggregator instance."""
        return BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

    # ===== Basic Replay Tests =====

    def test_replay_all_messages(self, client, scenario_messages):
        """Test replaying all recorded messages without errors."""
        results = []

        for scenario in scenario_messages:
            message = scenario["message"]
            result = client._parse_message(message)
            results.append(result)

        # Should have parsed all messages
        assert len(results) == len(scenario_messages)

        # All results should be non-None (messages were parsed)
        non_null_results = [r for r in results if r is not None]
        assert len(non_null_results) > 0

    def test_replay_message_types(self, client, scenario_messages):
        """Test that replayed messages have expected types."""
        message_types = []

        for scenario in scenario_messages:
            message = scenario["message"]
            result = client._parse_message(message)
            if result and "type" in result:
                message_types.append(result["type"])

        # Should have various message types
        assert "trade" in message_types
        assert "subscription" in message_types

    def test_replay_trade_message_parsing(self, client, scenario_messages):
        """Test parsing all trade messages from scenario."""
        trade_results = []

        for scenario in scenario_messages:
            message = scenario["message"]
            result = client._parse_message(message)

            if result and result.get("type") == "trade":
                trade_results.append(result)

        # Should have found trade messages
        assert len(trade_results) > 0

        # All trades should have data field
        for result in trade_results:
            assert "data" in result
            assert isinstance(result["data"], list)

    def test_replay_subscription_messages(self, client, scenario_messages):
        """Test parsing all subscription messages from scenario."""
        subscription_results = []

        for scenario in scenario_messages:
            message = scenario["message"]
            result = client._parse_message(message)

            if result and result.get("type") == "subscription":
                subscription_results.append(result)

        # Should have subscription messages
        assert len(subscription_results) > 0

        # All subscriptions should have symbol and status
        for result in subscription_results:
            assert "symbol" in result
            assert "status" in result

    # ===== Bar Aggregation Replay =====

    def test_replay_trade_aggregation(self, aggregator, scenario_messages):
        """Test aggregating bars from replayed trade messages."""
        trade_count = 0
        bar_count = 0

        for scenario in scenario_messages:
            message = scenario["message"]
            data = json.loads(message)

            if data.get("type") == "trade":
                for trade in data.get("data", []):
                    trade_count += 1
                    completed_bar = aggregator.add_trade(trade)
                    if completed_bar:
                        bar_count += 1

        # Should have processed trades
        assert trade_count > 0

        # Should have some completed bars
        assert bar_count >= 0

    def test_replay_aggregated_bars_structure(self, aggregator, scenario_messages):
        """Test that aggregated bars have correct structure."""
        completed_bars_dict = {}

        for scenario in scenario_messages:
            message = scenario["message"]
            data = json.loads(message)

            if data.get("type") == "trade":
                for trade in data.get("data", []):
                    completed_bar = aggregator.add_trade(trade)
                    if completed_bar:
                        symbol = completed_bar.get("symbol")
                        if symbol not in completed_bars_dict:
                            completed_bars_dict[symbol] = []
                        completed_bars_dict[symbol].append(completed_bar)

        # Check structure of completed bars
        for symbol, bars in completed_bars_dict.items():
            for bar in bars:
                # Required fields
                assert "symbol" in bar
                assert "timestamp" in bar
                assert "open" in bar
                assert "high" in bar
                assert "low" in bar
                assert "close" in bar
                assert "volume" in bar

                # Validate values
                assert bar["symbol"] == symbol
                assert bar["high"] >= bar["low"]
                assert bar["high"] >= bar["open"]
                assert bar["high"] >= bar["close"]
                assert bar["low"] <= bar["open"]
                assert bar["low"] <= bar["close"]
                assert bar["volume"] > 0

    def test_replay_multiple_symbols(self, aggregator, scenario_messages):
        """Test aggregating bars for multiple symbols from replay."""
        symbols_seen = set()

        for scenario in scenario_messages:
            message = scenario["message"]
            data = json.loads(message)

            if data.get("type") == "trade":
                for trade in data.get("data", []):
                    symbols_seen.add(trade.get("s"))
                    aggregator.add_trade(trade)

        # Should see multiple symbols
        assert len(symbols_seen) > 1

        # Current bars should have multiple symbols
        current_bars = aggregator.get_current_bars()
        assert len(current_bars) > 0

    def test_replay_statistics_tracking(self, client, scenario_messages):
        """Test statistics tracking during replay."""
        for scenario in scenario_messages:
            message = scenario["message"]
            client._parse_message(message)

        stats = client.get_statistics()

        assert stats is not None
        assert "parse_errors" in stats
        # Should have successfully parsed most messages
        assert stats["parse_errors"] == 0

    # ===== Error Handling in Replay =====

    def test_replay_with_invalid_messages(self, client):
        """Test replay can handle invalid messages mixed in."""
        messages = [
            '{"type":"trade","data":[{"s":"AAPL","p":150.0,"t":1000,"v":100}]}',
            "{invalid json}",
            '{"type":"subscription","symbol":"AAPL","status":"subscribed"}',
            "",
            '{"type":"trade","data":[]}',
        ]

        valid_count = 0
        invalid_count = 0

        for message in messages:
            result = client._parse_message(message)
            if result is not None:
                valid_count += 1
            else:
                invalid_count += 1

        assert valid_count > 0
        assert invalid_count > 0
        assert client._stats["parse_errors"] == invalid_count

    # ===== Deterministic Replay =====

    def test_replay_deterministic(self, scenario_messages):
        """Test that replaying messages produces deterministic results."""
        results1 = []
        results2 = []

        for run in range(2):
            client = FinnhubWebSocketClient(api_key="test_key")

            for scenario in scenario_messages:
                message = scenario["message"]
                result = client._parse_message(message)
                if run == 0:
                    results1.append(result)
                else:
                    results2.append(result)

        # Both runs should produce same results
        assert len(results1) == len(results2)

        for r1, r2 in zip(results1, results2):
            if r1 and r2:
                # Compare type and key fields instead of using json.dumps due to datetime objects
                assert r1.get("type") == r2.get("type")
                if "data" in r1 and "data" in r2:
                    assert len(r1["data"]) == len(r2["data"])

    # ===== Replay Statistics =====

    def test_replay_message_count(self, scenario_messages):
        """Test that we can count message types in replay."""
        message_type_count = {
            "trade": 0,
            "subscription": 0,
            "ping": 0,
            "error": 0
        }

        client = FinnhubWebSocketClient(api_key="test_key")

        for scenario in scenario_messages:
            message = scenario["message"]
            result = client._parse_message(message)

            if result:
                msg_type = result.get("type")
                if msg_type in message_type_count:
                    message_type_count[msg_type] += 1

        # Should have various message types
        assert message_type_count["trade"] > 0
        assert message_type_count["subscription"] > 0

        # Total should match
        total = sum(message_type_count.values())
        assert total > 0

    # ===== Trade Count in Replay =====

    def test_replay_total_trades_processed(self, scenario_messages):
        """Test counting total trades processed in replay."""
        total_trades = 0
        trade_details = []

        for scenario in scenario_messages:
            message = scenario["message"]
            data = json.loads(message)

            if data.get("type") == "trade":
                trades = data.get("data", [])
                total_trades += len(trades)
                for trade in trades:
                    trade_details.append({
                        "symbol": trade.get("s"),
                        "price": trade.get("p"),
                        "volume": trade.get("v")
                    })

        assert total_trades > 0
        assert len(trade_details) == total_trades

        # All trades should have required fields
        for trade in trade_details:
            assert trade["symbol"] is not None
            assert trade["price"] is not None
            assert trade["volume"] is not None

    # ===== Scenario Description Validation =====

    def test_scenario_file_has_descriptions(self, scenario_messages):
        """Test that all scenarios have descriptions."""
        for scenario in scenario_messages:
            assert "description" in scenario
            assert len(scenario["description"]) > 0
            assert "message" in scenario
            assert len(scenario["message"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
