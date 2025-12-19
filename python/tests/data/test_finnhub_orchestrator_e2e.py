"""
End-to-end tests for Finnhub WebSocket integration

Tests focus on data flow scenarios showing how bar aggregation works.
"""

import pytest
import pandas as pd
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import pytz

from src.data.finnhub_websocket import FinnhubWebSocketClient, BarAggregator


@pytest.mark.integration
class TestFinnhubE2E:
    """End-to-end tests for Finnhub integration."""

    # ===== Data Flow Tests =====

    def test_trade_to_bar_aggregation_flow(self):
        """Test trade data flowing through to bar aggregation."""
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        et = pytz.timezone("America/New_York")
        base_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))

        # Simulate trades
        trades = [
            {"s": "AAPL", "p": 150.0, "v": 100, "t": int(base_time.timestamp() * 1000)},
            {"s": "AAPL", "p": 150.1, "v": 200, "t": int((base_time.timestamp() + 30) * 1000)},
            {"s": "AAPL", "p": 150.2, "v": 150, "t": int((base_time.timestamp() + 60) * 1000)},
        ]

        for trade in trades:
            aggregator.add_trade(trade)

        # Check aggregated bar
        current_bars = aggregator.get_current_bars()
        assert "AAPL" in current_bars
        bar = current_bars["AAPL"]

        assert bar["open"] == 150.0
        assert bar["high"] == 150.2
        assert bar["low"] == 150.0
        assert bar["close"] == 150.2
        assert bar["volume"] == 450

    def test_bar_to_dataframe_flow(self):
        """Test bars flowing through to DataFrame for strategy."""
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        et = pytz.timezone("America/New_York")

        # Create bars for multiple 5-minute windows
        for minute in [0, 5, 10]:
            base_time = et.localize(datetime(2024, 11, 30, 9, 30 + minute, 0))

            trade = {
                "s": "AAPL",
                "p": 150.0 + minute,
                "v": 100 * (minute + 1),
                "t": int(base_time.timestamp() * 1000)
            }
            aggregator.add_trade(trade)

        # Force finalize to get all bars
        finalized = aggregator.force_finalize_all()
        bars = finalized.get("AAPL", [])

        # Convert to DataFrame
        df = aggregator.bars_to_dataframe(bars)

        assert not df.empty
        assert len(df) == 3
        assert "open" in df.columns
        assert "close" in df.columns

    def test_data_to_signals_conceptual_flow(self):
        """Test conceptual flow from data to signals (without full orchestrator)."""
        # This is a simplified test showing how data flows through the system

        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        et = pytz.timezone("America/New_York")
        base_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))

        # Simulate receiving trades
        for i in range(5):
            trade = {
                "s": "AAPL",
                "p": 150.0 + i * 0.1,
                "v": 100,
                "t": int((base_time.timestamp() + i * 60) * 1000)
            }
            aggregator.add_trade(trade)

        # Get bars for strategy
        current_bars = aggregator.get_current_bars()
        assert "AAPL" in current_bars

        bar = current_bars["AAPL"]
        # Strategy would then receive this bar data
        assert "open" in bar
        assert "close" in bar
        assert "volume" in bar
        assert "trade_count" in bar

    # ===== Error Handling E2E =====

    def test_e2e_invalid_trade_handling(self):
        """Test E2E handling of invalid trades."""
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        # Send invalid trades
        invalid_trades = [
            {"s": "AAPL", "p": 0, "v": 100, "t": 1638360000000},  # Zero price
            {"s": "", "p": 150, "v": 100, "t": 1638360000000},    # Empty symbol
            None,  # Null trade
        ]

        for trade in invalid_trades:
            if trade:
                aggregator.add_trade(trade)

        # Should handle gracefully without bars
        current_bars = aggregator.get_current_bars()
        assert len(current_bars) == 0

    # ===== Multi-Symbol E2E =====

    def test_e2e_multi_symbol_aggregation(self):
        """Test E2E aggregation with multiple symbols."""
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        et = pytz.timezone("America/New_York")
        base_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))

        # Trades for multiple symbols
        trades = [
            {"s": "AAPL", "p": 150.0, "v": 100, "t": int(base_time.timestamp() * 1000)},
            {"s": "MSFT", "p": 350.0, "v": 50, "t": int(base_time.timestamp() * 1000)},
            {"s": "NVDA", "p": 500.0, "v": 25, "t": int(base_time.timestamp() * 1000)},
        ]

        for trade in trades:
            aggregator.add_trade(trade)

        # Verify all symbols aggregated
        current_bars = aggregator.get_current_bars()
        assert len(current_bars) == 3
        assert "AAPL" in current_bars
        assert "MSFT" in current_bars
        assert "NVDA" in current_bars

    # ===== Consistency Tests =====

    def test_e2e_deterministic_bar_aggregation(self):
        """Test that bar aggregation is deterministic."""
        et = pytz.timezone("America/New_York")
        base_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))

        trades = [
            {"s": "AAPL", "p": 150.0, "v": 100, "t": int(base_time.timestamp() * 1000)},
            {"s": "AAPL", "p": 150.1, "v": 200, "t": int((base_time.timestamp() + 30) * 1000)},
            {"s": "AAPL", "p": 150.2, "v": 150, "t": int((base_time.timestamp() + 60) * 1000)},
        ]

        # Run twice and compare
        results = []
        for run in range(2):
            aggregator = BarAggregator(
                bar_interval="5m",
                timezone="America/New_York",
                bar_delay_seconds=0
            )

            for trade in trades:
                aggregator.add_trade(trade)

            current_bars = aggregator.get_current_bars()
            results.append(current_bars["AAPL"])

        # Results should be identical
        assert results[0]["open"] == results[1]["open"]
        assert results[0]["high"] == results[1]["high"]
        assert results[0]["low"] == results[1]["low"]
        assert results[0]["close"] == results[1]["close"]
        assert results[0]["volume"] == results[1]["volume"]

    def test_e2e_message_to_bar_flow(self):
        """Test end-to-end flow from WebSocket message to aggregated bar."""
        client = FinnhubWebSocketClient(api_key="test_key")
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        # Simulate WebSocket message
        message = json.dumps({
            "type": "trade",
            "data": [
                {"s": "AAPL", "p": 150.0, "t": 1699892400000, "v": 100},
                {"s": "AAPL", "p": 150.1, "t": 1699892401000, "v": 150},
            ]
        })

        # Parse the message
        parsed = client._parse_message(message)
        assert parsed is not None
        assert parsed["type"] == "trade"

        # Feed trades to aggregator
        for trade in parsed["data"]:
            aggregator.add_trade(trade)

        # Verify bar aggregation
        current_bars = aggregator.get_current_bars()
        assert "AAPL" in current_bars
        bar = current_bars["AAPL"]
        assert bar["volume"] == 250

    def test_e2e_multiple_bar_cycles(self):
        """Test multiple trading cycles with bar completion."""
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        et = pytz.timezone("America/New_York")
        completed_bars_list = []

        # Simulate 3 bars worth of trades
        for bar_num in range(3):
            base_time = et.localize(datetime(2024, 11, 30, 9, 30 + bar_num * 5, 0))

            # Trades within bar
            for minute_offset in range(0, 5):
                trade = {
                    "s": "AAPL",
                    "p": 150.0 + bar_num * 0.5 + minute_offset * 0.01,
                    "v": 100,
                    "t": int((base_time.timestamp() + minute_offset * 60) * 1000)
                }

                completed_bar = aggregator.add_trade(trade)
                if completed_bar:
                    completed_bars_list.append(completed_bar)

        # Force finalize remaining bar
        finalized = aggregator.force_finalize_all()
        if "AAPL" in finalized:
            completed_bars_list.extend(finalized["AAPL"])

        # Should have completed bars
        assert len(completed_bars_list) > 0

    def test_e2e_websocket_to_dataframe(self):
        """Test full E2E pipeline: WebSocket message -> parse -> aggregate -> DataFrame."""
        client = FinnhubWebSocketClient(api_key="test_key")
        aggregator = BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

        # Simulate multiple WebSocket messages
        messages = [
            json.dumps({"type": "trade", "data": [
                {"s": "AAPL", "p": 150.0, "t": 1699892400000, "v": 100},
                {"s": "AAPL", "p": 150.1, "t": 1699892401000, "v": 200}
            ]}),
            json.dumps({"type": "trade", "data": [
                {"s": "AAPL", "p": 150.2, "t": 1699892402000, "v": 150}
            ]}),
        ]

        all_bars = []

        for message in messages:
            parsed = client._parse_message(message)
            if parsed and parsed.get("type") == "trade":
                for trade in parsed["data"]:
                    completed_bar = aggregator.add_trade(trade)
                    if completed_bar:
                        all_bars.append(completed_bar)

        # Get current bar
        current_bars = aggregator.get_current_bars()
        if "AAPL" in current_bars:
            # Create DataFrame from current bar
            df = aggregator.bars_to_dataframe([current_bars["AAPL"]])
            assert not df.empty
            assert "open" in df.columns
            assert len(df) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
