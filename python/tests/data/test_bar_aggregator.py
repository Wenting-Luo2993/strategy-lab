"""
Unit tests for BarAggregator

Tests the bar aggregation logic that converts tick-level trades to OHLCV bars.
"""

import pytest
import pandas as pd
from datetime import datetime
import pytz

from src.data.finnhub_websocket import BarAggregator


class TestBarAggregator:
    """Test suite for BarAggregator class."""

    @pytest.fixture
    def aggregator_5m(self):
        """Create 5-minute bar aggregator."""
        return BarAggregator(
            bar_interval="5m",
            timezone="America/New_York",
            bar_delay_seconds=0  # No delay for testing
        )

    @pytest.fixture
    def aggregator_1m(self):
        """Create 1-minute bar aggregator."""
        return BarAggregator(
            bar_interval="1m",
            timezone="America/New_York",
            bar_delay_seconds=0
        )

    def test_parse_interval(self, aggregator_5m):
        """Test interval parsing."""
        assert aggregator_5m._parse_interval("1m") == 60
        assert aggregator_5m._parse_interval("5m") == 300
        assert aggregator_5m._parse_interval("15m") == 900
        assert aggregator_5m._parse_interval("1h") == 3600
        assert aggregator_5m._parse_interval("30s") == 30

    def test_parse_interval_invalid(self, aggregator_5m):
        """Test invalid interval format."""
        with pytest.raises(ValueError):
            aggregator_5m._parse_interval("invalid")

    def test_single_trade(self, aggregator_5m):
        """Test processing single trade."""
        # Trade at 09:31:30 (within 09:30-09:35 bar)
        et = pytz.timezone("America/New_York")
        trade_time = et.localize(datetime(2024, 11, 30, 9, 31, 30))
        trade_timestamp_ms = int(trade_time.timestamp() * 1000)

        trade = {
            "s": "AAPL",
            "p": 150.25,
            "v": 100,
            "t": trade_timestamp_ms
        }

        completed_bar = aggregator_5m.add_trade(trade)

        # First trade should not complete a bar
        assert completed_bar is None

        # Check current bar state
        current_bars = aggregator_5m.get_current_bars()
        assert "AAPL" in current_bars

        bar = current_bars["AAPL"]
        assert bar["open"] == 150.25
        assert bar["high"] == 150.25
        assert bar["low"] == 150.25
        assert bar["close"] == 150.25
        assert bar["volume"] == 100
        assert bar["trade_count"] == 1

    def test_multiple_trades_same_bar(self, aggregator_5m):
        """Test multiple trades within same bar period."""
        et = pytz.timezone("America/New_York")
        base_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))

        trades = [
            {"s": "AAPL", "p": 150.00, "v": 100, "t": int(base_time.timestamp() * 1000)},
            {"s": "AAPL", "p": 150.50, "v": 150, "t": int((base_time.timestamp() + 60) * 1000)},
            {"s": "AAPL", "p": 149.75, "v": 200, "t": int((base_time.timestamp() + 120) * 1000)},
            {"s": "AAPL", "p": 150.25, "v": 50, "t": int((base_time.timestamp() + 180) * 1000)},
        ]

        for trade in trades:
            completed_bar = aggregator_5m.add_trade(trade)
            assert completed_bar is None  # All within same 5m bar

        # Check aggregated bar
        current_bars = aggregator_5m.get_current_bars()
        bar = current_bars["AAPL"]

        assert bar["open"] == 150.00   # First trade
        assert bar["high"] == 150.50   # Max price
        assert bar["low"] == 149.75    # Min price
        assert bar["close"] == 150.25  # Last trade
        assert bar["volume"] == 500    # Sum of volumes
        assert bar["trade_count"] == 4

    def test_bar_completion(self, aggregator_5m):
        """Test bar completion when crossing interval boundary."""
        et = pytz.timezone("America/New_York")

        # First bar: 09:30-09:35
        trade1_time = et.localize(datetime(2024, 11, 30, 9, 32, 0))
        trade1 = {
            "s": "AAPL",
            "p": 150.00,
            "v": 100,
            "t": int(trade1_time.timestamp() * 1000)
        }

        completed_bar = aggregator_5m.add_trade(trade1)
        assert completed_bar is None  # First bar not complete

        # Second bar: 09:35-09:40 (crosses boundary)
        trade2_time = et.localize(datetime(2024, 11, 30, 9, 35, 0))
        trade2 = {
            "s": "AAPL",
            "p": 151.00,
            "v": 200,
            "t": int(trade2_time.timestamp() * 1000)
        }

        completed_bar = aggregator_5m.add_trade(trade2)

        # First bar should be completed
        assert completed_bar is not None
        assert completed_bar["symbol"] == "AAPL"
        assert completed_bar["open"] == 150.00
        assert completed_bar["close"] == 150.00
        assert completed_bar["volume"] == 100

        # New current bar should be started
        current_bars = aggregator_5m.get_current_bars()
        bar = current_bars["AAPL"]
        assert bar["open"] == 151.00
        assert bar["close"] == 151.00
        assert bar["volume"] == 200

    def test_multiple_symbols(self, aggregator_5m):
        """Test aggregating bars for multiple symbols simultaneously."""
        et = pytz.timezone("America/New_York")
        trade_time = et.localize(datetime(2024, 11, 30, 9, 32, 0))
        timestamp_ms = int(trade_time.timestamp() * 1000)

        trades = [
            {"s": "AAPL", "p": 150.00, "v": 100, "t": timestamp_ms},
            {"s": "MSFT", "p": 350.00, "v": 50, "t": timestamp_ms},
            {"s": "AAPL", "p": 150.25, "v": 200, "t": timestamp_ms + 1000},
            {"s": "NVDA", "p": 500.00, "v": 75, "t": timestamp_ms + 2000},
        ]

        for trade in trades:
            aggregator_5m.add_trade(trade)

        # Check all symbols have current bars
        current_bars = aggregator_5m.get_current_bars()
        assert len(current_bars) == 3
        assert "AAPL" in current_bars
        assert "MSFT" in current_bars
        assert "NVDA" in current_bars

        # Verify AAPL aggregation (2 trades)
        assert current_bars["AAPL"]["open"] == 150.00
        assert current_bars["AAPL"]["close"] == 150.25
        assert current_bars["AAPL"]["volume"] == 300
        assert current_bars["AAPL"]["trade_count"] == 2

    def test_timezone_conversion(self, aggregator_5m):
        """Test correct timezone handling for bar timestamps."""
        # Create trade with UTC timestamp
        utc_time = datetime(2024, 11, 30, 14, 32, 0, tzinfo=pytz.UTC)  # 14:32 UTC
        trade = {
            "s": "AAPL",
            "p": 150.00,
            "v": 100,
            "t": int(utc_time.timestamp() * 1000)
        }

        aggregator_5m.add_trade(trade)

        current_bars = aggregator_5m.get_current_bars()
        bar_timestamp = current_bars["AAPL"]["timestamp"]

        # 14:32 UTC = 09:32 ET (during standard time)
        # Should be aligned to 09:30 ET bar
        assert bar_timestamp.hour == 9
        assert bar_timestamp.minute == 30
        assert bar_timestamp.tzinfo.zone == "America/New_York"

    def test_1_minute_bars(self, aggregator_1m):
        """Test 1-minute bar aggregation."""
        et = pytz.timezone("America/New_York")

        # Trades in 09:30 bar
        trade1_time = et.localize(datetime(2024, 11, 30, 9, 30, 15))
        trade1 = {"s": "AAPL", "p": 150.00, "v": 100, "t": int(trade1_time.timestamp() * 1000)}

        # Trade in 09:31 bar (should complete 09:30 bar)
        trade2_time = et.localize(datetime(2024, 11, 30, 9, 31, 0))
        trade2 = {"s": "AAPL", "p": 151.00, "v": 200, "t": int(trade2_time.timestamp() * 1000)}

        completed_bar = aggregator_1m.add_trade(trade1)
        assert completed_bar is None

        completed_bar = aggregator_1m.add_trade(trade2)
        assert completed_bar is not None
        assert completed_bar["timestamp"].minute == 30  # 09:30 bar completed

    def test_invalid_trade_data(self, aggregator_5m):
        """Test handling of invalid trade data."""
        invalid_trades = [
            {"s": "AAPL", "p": 0, "v": 100, "t": 1638360000000},  # Zero price
            {"s": "AAPL", "p": 150, "v": 0, "t": 1638360000000},  # Zero volume
            {"s": "", "p": 150, "v": 100, "t": 1638360000000},    # Empty symbol
            {"p": 150, "v": 100, "t": 1638360000000},             # Missing symbol
        ]

        for trade in invalid_trades:
            completed_bar = aggregator_5m.add_trade(trade)
            assert completed_bar is None

        # No bars should be created
        current_bars = aggregator_5m.get_current_bars()
        assert len(current_bars) == 0

    def test_get_completed_bars(self, aggregator_5m):
        """Test retrieving completed bars."""
        et = pytz.timezone("America/New_York")

        # Create and complete a bar for AAPL
        trade1_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))
        trade1 = {"s": "AAPL", "p": 150.00, "v": 100, "t": int(trade1_time.timestamp() * 1000)}

        trade2_time = et.localize(datetime(2024, 11, 30, 9, 35, 0))
        trade2 = {"s": "AAPL", "p": 151.00, "v": 200, "t": int(trade2_time.timestamp() * 1000)}

        aggregator_5m.add_trade(trade1)
        aggregator_5m.add_trade(trade2)  # Completes first bar

        # Get completed bars
        completed_bars = aggregator_5m.get_completed_bars(clear=False)

        assert "AAPL" in completed_bars
        assert len(completed_bars["AAPL"]) == 1

        bar = completed_bars["AAPL"][0]
        assert bar["symbol"] == "AAPL"
        assert bar["open"] == 150.00
        assert bar["close"] == 150.00

    def test_get_completed_bars_clear(self, aggregator_5m):
        """Test clearing completed bars after retrieval."""
        et = pytz.timezone("America/New_York")

        trade1_time = et.localize(datetime(2024, 11, 30, 9, 30, 0))
        trade1 = {"s": "AAPL", "p": 150.00, "v": 100, "t": int(trade1_time.timestamp() * 1000)}

        trade2_time = et.localize(datetime(2024, 11, 30, 9, 35, 0))
        trade2 = {"s": "AAPL", "p": 151.00, "v": 200, "t": int(trade2_time.timestamp() * 1000)}

        aggregator_5m.add_trade(trade1)
        aggregator_5m.add_trade(trade2)

        # Get and clear
        completed_bars = aggregator_5m.get_completed_bars(clear=True)
        assert len(completed_bars["AAPL"]) == 1

        # Should be empty now
        completed_bars = aggregator_5m.get_completed_bars(clear=False)
        assert len(completed_bars.get("AAPL", [])) == 0

    def test_force_finalize_all(self, aggregator_5m):
        """Test forcing finalization of all current bars."""
        et = pytz.timezone("America/New_York")
        trade_time = et.localize(datetime(2024, 11, 30, 9, 32, 0))
        timestamp_ms = int(trade_time.timestamp() * 1000)

        # Add trades for multiple symbols
        trades = [
            {"s": "AAPL", "p": 150.00, "v": 100, "t": timestamp_ms},
            {"s": "MSFT", "p": 350.00, "v": 50, "t": timestamp_ms},
        ]

        for trade in trades:
            aggregator_5m.add_trade(trade)

        # Force finalize
        finalized_bars = aggregator_5m.force_finalize_all()

        # Should have bars for both symbols
        assert "AAPL" in finalized_bars
        assert "MSFT" in finalized_bars
        assert len(finalized_bars["AAPL"]) == 1
        assert len(finalized_bars["MSFT"]) == 1

        # Current bars should be empty
        current_bars = aggregator_5m.get_current_bars()
        assert len(current_bars) == 0

    def test_bars_to_dataframe(self, aggregator_5m):
        """Test converting bars to DataFrame."""
        et = pytz.timezone("America/New_York")

        # Create multiple completed bars
        for minute in [30, 35, 40]:
            trade_time = et.localize(datetime(2024, 11, 30, 9, minute, 0))
            trade = {
                "s": "AAPL",
                "p": 150.00 + minute,
                "v": 100,
                "t": int(trade_time.timestamp() * 1000)
            }
            aggregator_5m.add_trade(trade)

        # Force finalize to get all bars
        finalized_bars = aggregator_5m.force_finalize_all()
        bars_list = finalized_bars["AAPL"]

        # Convert to DataFrame
        df = aggregator_5m.bars_to_dataframe(bars_list)

        # Check DataFrame structure
        assert not df.empty
        assert len(df) == 3
        assert df.index.name == "timestamp"
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "close" in df.columns
        assert "volume" in df.columns

    def test_statistics(self, aggregator_5m):
        """Test aggregator statistics tracking."""
        et = pytz.timezone("America/New_York")
        trade_time = et.localize(datetime(2024, 11, 30, 9, 32, 0))
        timestamp_ms = int(trade_time.timestamp() * 1000)

        # Process some trades
        for i in range(5):
            trade = {
                "s": "AAPL",
                "p": 150.00 + i,
                "v": 100,
                "t": timestamp_ms + i * 1000
            }
            aggregator_5m.add_trade(trade)

        stats = aggregator_5m.get_statistics()

        assert stats["trades_processed"] == 5
        assert stats["tickers_active"] == 1
        assert stats["current_bars_count"] == 1
        assert stats["bars_completed"] == 0  # No bar completed yet


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
