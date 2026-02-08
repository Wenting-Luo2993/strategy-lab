"""
Unit tests for trading bot data layer (Phase 2).
"""

import asyncio
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pandas as pd
import pytest
import pytz

from vibe.trading_bot.data.aggregator import BarAggregator
from vibe.trading_bot.data.cache import DataCache
from vibe.trading_bot.data.manager import DataManager
from vibe.trading_bot.data.providers.base import LiveDataProvider, ProviderHealth, RateLimiter
from vibe.trading_bot.data.providers.finnhub import (
    ConnectionState,
    FinnhubWebSocketClient,
)
from vibe.trading_bot.data.providers.yahoo import YahooDataProvider


# ============================================================================
# Task 2.1: LiveDataProvider Base Class Tests
# ============================================================================


class TestRateLimiter:
    """Tests for RateLimiter."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_requests_within_limit(self):
        """Rate limiter allows requests within configured rate."""
        limiter = RateLimiter(rate=5, period=1.0)

        # First request should be immediate
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_exceeding_rate(self):
        """Rate limiter blocks when rate exceeded."""
        limiter = RateLimiter(rate=2, period=1.0)

        # Acquire 2 tokens (should be immediate)
        await limiter.acquire()
        await limiter.acquire()

        # Third request should wait
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        # Should wait at least 0.4s (half a period for 2 req/sec)
        assert elapsed >= 0.3


class TestLiveDataProvider:
    """Tests for LiveDataProvider base class."""

    def test_live_provider_extends_base(self):
        """LiveDataProvider extends vibe.common.data.DataProvider."""
        from vibe.common.data import DataProvider
        assert issubclass(LiveDataProvider, DataProvider)

    def test_provider_initialization(self):
        """Provider initializes with correct parameters."""
        provider = YahooDataProvider(
            rate_limit=10,
            rate_limit_period=1.0,
            max_retries=5,
        )

        assert provider.rate_limit == 10
        assert provider.max_retries == 5
        assert provider.is_healthy

    def test_provider_health_status_tracking(self):
        """Provider tracks health status."""
        provider = YahooDataProvider()

        assert provider.health == ProviderHealth.HEALTHY
        status = provider.get_health_status()

        assert status["status"] == "healthy"
        assert status["successful_requests"] == 0
        assert status["failed_requests"] == 0

    def test_provider_reset_health(self):
        """Provider can reset health status."""
        provider = YahooDataProvider()

        # Simulate some activity
        provider._successful_requests = 10
        provider._failed_requests = 2

        provider.reset_health()

        assert provider._successful_requests == 0
        assert provider._failed_requests == 0
        assert provider.health == ProviderHealth.HEALTHY


# ============================================================================
# Task 2.2: YahooDataProvider Tests
# ============================================================================


class TestYahooDataProvider:
    """Tests for Yahoo Finance data provider."""

    @pytest.mark.asyncio
    async def test_provider_initialization(self):
        """Provider initializes correctly."""
        provider = YahooDataProvider(rate_limit=5)
        assert provider.rate_limit == 5

    @pytest.mark.asyncio
    @patch("yfinance.Ticker")
    async def test_get_historical_success(self, mock_ticker_class):
        """Successfully fetches historical data."""
        # Setup mock
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker

        # Create sample data
        sample_data = {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [1000000, 1100000, 1200000],
        }
        dates = pd.date_range("2024-01-01", periods=3, freq="5min")
        sample_df = pd.DataFrame(sample_data, index=dates)

        mock_ticker.history.return_value = sample_df

        # Test
        provider = YahooDataProvider()
        result = await provider.get_historical(
            "AAPL",
            period="1d",
            interval="5m",
        )

        # Verify
        assert not result.empty
        assert len(result) == 3
        assert all(
            col in result.columns
            for col in ["open", "high", "low", "close", "volume"]
        )

    @pytest.mark.asyncio
    @patch("yfinance.Ticker")
    async def test_get_historical_with_start_end_times(self, mock_ticker_class):
        """Fetches data with start and end times."""
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker

        sample_data = {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000000],
        }
        dates = pd.date_range("2024-01-01", periods=1, freq="5min")
        sample_df = pd.DataFrame(sample_data, index=dates)

        mock_ticker.history.return_value = sample_df

        provider = YahooDataProvider()
        start_time = datetime(2024, 1, 1)
        end_time = datetime(2024, 1, 2)

        result = await provider.get_historical(
            "AAPL",
            start_time=start_time,
            end_time=end_time,
            interval="5m",
        )

        assert not result.empty

    @pytest.mark.asyncio
    async def test_invalid_symbol_raises_error(self):
        """Invalid symbol raises ValueError."""
        provider = YahooDataProvider()

        with pytest.raises(ValueError):
            await provider.get_historical("", interval="5m")

    @pytest.mark.asyncio
    async def test_invalid_interval_raises_error(self):
        """Invalid interval raises ValueError."""
        provider = YahooDataProvider()

        with pytest.raises(ValueError):
            await provider.get_historical("AAPL", interval="invalid")

    @pytest.mark.asyncio
    @patch("yfinance.Ticker")
    async def test_retry_with_backoff(self, mock_ticker_class):
        """Provider retries with exponential backoff."""
        # Setup mock to fail twice then succeed
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker

        sample_data = {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000000],
        }
        dates = pd.date_range("2024-01-01", periods=1, freq="5min")
        sample_df = pd.DataFrame(sample_data, index=dates)

        # Fail twice, then succeed
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("Network error" if call_count[0] == 1 else "Timeout")
            return sample_df

        mock_ticker.history.side_effect = side_effect

        provider = YahooDataProvider(
            max_retries=3,
            retry_backoff_base=0.01,  # Very short delay for testing
        )

        result = await provider.get_historical("AAPL", interval="5m")

        assert not result.empty
        assert call_count[0] == 3

    @pytest.mark.asyncio
    @patch("yfinance.Ticker")
    async def test_rate_limiting_enforced(self, mock_ticker_class):
        """Rate limiter prevents exceeding configured rate."""
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker

        sample_data = {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000000],
        }
        dates = pd.date_range("2024-01-01", periods=1, freq="5min")
        sample_df = pd.DataFrame(sample_data, index=dates)
        mock_ticker.history.return_value = sample_df

        # Create provider with low rate limit
        provider = YahooDataProvider(rate_limit=2, rate_limit_period=1.0)

        # Make 3 requests
        start = time.time()

        for _ in range(3):
            await provider.get_historical("AAPL", interval="5m")

        elapsed = time.time() - start

        # Should take at least 0.4s (2 req per second)
        assert elapsed >= 0.3


# ============================================================================
# Task 2.3: FinnhubWebSocketClient Tests
# ============================================================================


class TestFinnhubWebSocketClient:
    """Tests for Finnhub WebSocket client."""

    def test_client_initialization(self):
        """Client initializes with correct state."""
        client = FinnhubWebSocketClient(api_key="test_key")

        assert client.state == ConnectionState.DISCONNECTED
        assert not client.connected
        assert len(client.subscribed_symbols) == 0

    def test_event_callbacks_registration(self):
        """Event callbacks can be registered."""
        client = FinnhubWebSocketClient(api_key="test_key")

        on_connected = Mock()
        on_trade = Mock()

        client.on_connected(on_connected)
        client.on_trade(on_trade)

        assert client._on_connected == on_connected
        assert client._on_trade == on_trade

    @pytest.mark.asyncio
    async def test_handle_message_with_trade_data(self):
        """Client handles trade messages correctly."""
        client = FinnhubWebSocketClient(api_key="test_key")

        trade_events = []

        async def on_trade(trade):
            trade_events.append(trade)

        client.on_trade(on_trade)

        # Simulate trade message
        message = {
            "s": "AAPL",
            "data": [
                {
                    "p": 150.25,
                    "s": 100,
                    "t": 1704067200000,
                    "bp": 150.20,
                    "ap": 150.30,
                }
            ],
        }

        await client._handle_message(message)

        assert len(trade_events) == 1
        assert trade_events[0]["symbol"] == "AAPL"
        assert trade_events[0]["price"] == 150.25
        assert trade_events[0]["size"] == 100


# ============================================================================
# Task 2.4: BarAggregator Tests
# ============================================================================


class TestBarAggregator:
    """Tests for bar aggregator."""

    def test_aggregator_initialization(self):
        """Aggregator initializes with correct parameters."""
        aggregator = BarAggregator(bar_interval="5m")

        assert aggregator.bar_interval == "5m"
        assert aggregator.interval_seconds == 300

    def test_invalid_interval_raises_error(self):
        """Invalid interval raises error."""
        with pytest.raises(ValueError):
            BarAggregator(bar_interval="invalid")

    def test_get_bar_start_time(self):
        """Bar start time calculated correctly."""
        aggregator = BarAggregator(bar_interval="5m")

        # 09:32:30 should belong to 09:30 bar
        ts = datetime(2024, 1, 15, 9, 32, 30)
        start = aggregator._get_bar_start_time(ts)

        assert start.hour == 9
        assert start.minute == 30
        assert start.second == 0

    def test_bar_aggregation_5m(self):
        """Trades aggregate into 5-minute bars."""
        aggregator = BarAggregator(bar_interval="5m")

        completed_bars = []

        def on_bar_complete(bar):
            completed_bars.append(bar)

        aggregator.on_bar_complete(on_bar_complete)

        # Add trades within same bar
        ts1 = datetime(2024, 1, 15, 9, 30, 0)
        ts2 = datetime(2024, 1, 15, 9, 31, 0)
        ts3 = datetime(2024, 1, 15, 9, 32, 0)

        aggregator.add_trade(ts1, 100.0, 100)
        aggregator.add_trade(ts2, 101.0, 150)
        aggregator.add_trade(ts3, 99.5, 200)

        # No bar complete yet (still within 09:30 bar)
        assert len(completed_bars) == 0

        # Add trade in next bar
        ts4 = datetime(2024, 1, 15, 9, 35, 0)
        bar = aggregator.add_trade(ts4, 102.0, 100)

        # Previous bar should complete
        assert bar is not None
        assert bar["open"] == 100.0
        assert bar["high"] == 101.0
        assert bar["low"] == 99.5
        assert bar["close"] == 99.5
        assert bar["volume"] == 450
        assert bar["trade_count"] == 3

    def test_bar_boundary_crossing(self):
        """New bar starts when period boundary crossed."""
        aggregator = BarAggregator(bar_interval="5m")

        # Add trade in 09:30 bar
        ts1 = datetime(2024, 1, 15, 9, 34, 59)
        aggregator.add_trade(ts1, 100.0, 100)

        # Add trade in 09:35 bar - should complete 09:30 bar
        ts2 = datetime(2024, 1, 15, 9, 35, 0)
        completed = aggregator.add_trade(ts2, 101.0, 100)

        assert completed is not None
        assert completed["timestamp"].minute == 30

        # Current bar should be for 09:35
        assert aggregator.current_bar_start_time.minute == 35

    def test_late_trade_handling(self):
        """Late trades handled correctly."""
        aggregator = BarAggregator(bar_interval="5m", late_trade_handling="previous")

        # Add trade in 09:30 bar
        ts1 = datetime(2024, 1, 15, 9, 30, 0)
        aggregator.add_trade(ts1, 100.0, 100)

        # Move to 09:35 bar
        ts2 = datetime(2024, 1, 15, 9, 35, 0)
        aggregator.add_trade(ts2, 101.0, 100)

        # Add late trade for 09:30 bar
        ts3 = datetime(2024, 1, 15, 9, 32, 0)
        result = aggregator.add_trade(ts3, 99.5, 100)

        assert result is None  # No new bar completed
        assert aggregator.late_trades_count == 1

    def test_flush_completes_current_bar(self):
        """Flush completes current bar."""
        aggregator = BarAggregator(bar_interval="5m")

        ts = datetime(2024, 1, 15, 9, 30, 0)
        aggregator.add_trade(ts, 100.0, 100)

        bar = aggregator.flush()

        assert bar is not None
        assert bar["close"] == 100.0
        assert aggregator.current_bar is None


# ============================================================================
# Task 2.5: DataCache Tests
# ============================================================================


class TestDataCache:
    """Tests for data cache."""

    def test_cache_initialization(self):
        """Cache initializes correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            assert cache.cache_dir == Path(tmpdir)
            assert cache.ttl_seconds == 3600

    def test_cache_put_and_get(self):
        """Data can be cached and retrieved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            # Create sample data
            data = {
                "timestamp": pd.date_range("2024-01-01", periods=10),
                "open": [100.0 + i for i in range(10)],
                "high": [101.0 + i for i in range(10)],
                "low": [99.0 + i for i in range(10)],
                "close": [100.5 + i for i in range(10)],
                "volume": [1000000 + i * 100000 for i in range(10)],
            }
            df = pd.DataFrame(data)

            # Cache the data
            cache.put("AAPL", "5m", df)

            # Retrieve the data
            retrieved = cache.get("AAPL", "5m")

            assert retrieved is not None
            assert len(retrieved) == 10
            assert cache.stats()["hits"] == 1

    def test_cache_hit_increases_counter(self):
        """Cache hits are counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            data = {
                "timestamp": pd.date_range("2024-01-01", periods=1),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000000],
            }
            df = pd.DataFrame(data)

            cache.put("AAPL", "5m", df)

            # First get
            cache.get("AAPL", "5m")
            # Second get
            cache.get("AAPL", "5m")

            stats = cache.stats()
            assert stats["hits"] == 2

    def test_cache_ttl_expiry(self):
        """Cache expires after TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir), ttl_seconds=1)

            data = {
                "timestamp": pd.date_range("2024-01-01", periods=1),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000000],
            }
            df = pd.DataFrame(data)

            cache.put("AAPL", "5m", df)

            # Should be available immediately
            retrieved = cache.get("AAPL", "5m")
            assert retrieved is not None

            # Wait for TTL to expire
            time.sleep(1.1)

            # Should be expired
            retrieved = cache.get("AAPL", "5m")
            assert retrieved is None

    def test_cache_clear(self):
        """Cache can be cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            data = {
                "timestamp": pd.date_range("2024-01-01", periods=1),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000000],
            }
            df = pd.DataFrame(data)

            cache.put("AAPL", "5m", df)
            cache.put("MSFT", "5m", df)

            # Clear specific symbol
            cache.clear("AAPL")

            aapl_cached = cache.get("AAPL", "5m")
            msft_cached = cache.get("MSFT", "5m")

            assert aapl_cached is None
            assert msft_cached is not None

    def test_cache_stats(self):
        """Cache statistics available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DataCache(Path(tmpdir))

            data = {
                "timestamp": pd.date_range("2024-01-01", periods=1),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000000],
            }
            df = pd.DataFrame(data)

            cache.put("AAPL", "5m", df)
            cache.get("AAPL", "5m")
            cache.get("AAPL", "5m")

            stats = cache.stats()

            assert stats["hits"] == 2
            assert stats["misses"] == 0
            assert stats["cached_items"] == 1
            assert stats["hit_rate"] == 100.0


# ============================================================================
# Task 2.6: DataManager Tests
# ============================================================================


class TestDataManager:
    """Tests for data manager."""

    @pytest.mark.asyncio
    async def test_data_manager_initialization(self):
        """DataManager initializes correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = YahooDataProvider()
            manager = DataManager(
                provider=provider,
                cache_dir=Path(tmpdir),
            )

            assert manager.provider == provider
            assert manager.cache is not None

    @pytest.mark.asyncio
    @patch("yfinance.Ticker")
    async def test_data_manager_cache_first(self, mock_ticker_class):
        """DataManager checks cache before provider."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup cache with data
            cache_dir = Path(tmpdir)
            cache = DataCache(cache_dir)

            sample_data = {
                "timestamp": pd.date_range("2024-01-01", periods=5),
                "open": [100.0 + i for i in range(5)],
                "high": [101.0 + i for i in range(5)],
                "low": [99.0 + i for i in range(5)],
                "close": [100.5 + i for i in range(5)],
                "volume": [1000000 + i * 100000 for i in range(5)],
            }
            df = pd.DataFrame(sample_data)
            cache.put("AAPL", "5m", df)

            # Setup provider
            provider = MagicMock(spec=YahooDataProvider)

            # Create manager
            manager = DataManager(provider=provider, cache_dir=cache_dir)

            # Get data (should hit cache)
            result = await manager.get_data("AAPL", "5m")

            # Verify cache was used
            assert len(result) == 5
            provider.get_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_data_manager_call_provider_on_cache_miss(self):
        """DataManager calls provider on cache miss."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup provider mock
            provider = AsyncMock(spec=YahooDataProvider)

            sample_data = {
                "timestamp": pd.date_range("2024-01-01", periods=5),
                "open": [100.0 + i for i in range(5)],
                "high": [101.0 + i for i in range(5)],
                "low": [99.0 + i for i in range(5)],
                "close": [100.5 + i for i in range(5)],
                "volume": [1000000 + i * 100000 for i in range(5)],
            }
            sample_df = pd.DataFrame(sample_data)
            provider.get_bars.return_value = sample_df

            # Create manager with empty cache
            manager = DataManager(
                provider=provider,
                cache_dir=Path(tmpdir),
            )

            # Get data (should call provider)
            result = await manager.get_data("AAPL", "5m")

            # Verify provider was called
            provider.get_bars.assert_called_once()
            assert len(result) == 5

    @pytest.mark.asyncio
    async def test_data_manager_gap_detection(self):
        """DataManager detects gaps in data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup provider with gapped data
            provider = AsyncMock(spec=YahooDataProvider)

            # Create data with gap (missing bar)
            dates = [
                datetime(2024, 1, 1, 9, 30),
                datetime(2024, 1, 1, 9, 35),
                datetime(2024, 1, 1, 9, 45),  # Gap here (should be 9:40)
            ]
            sample_data = {
                "timestamp": dates,
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1000000, 1100000, 1200000],
            }
            sample_df = pd.DataFrame(sample_data)
            provider.get_bars.return_value = sample_df

            gap_detected = {"called": False, "count": 0}

            def on_gap(gap_info):
                gap_detected["called"] = True
                gap_detected["count"] = gap_info["gap_count"]

            manager = DataManager(
                provider=provider,
                cache_dir=Path(tmpdir),
            )
            manager.on_data_gap(on_gap)

            # Get data
            result = await manager.get_data("AAPL", "5m")

            # Verify gap was detected
            assert gap_detected["called"]
            assert gap_detected["count"] > 0

    @pytest.mark.asyncio
    async def test_data_manager_metrics(self):
        """DataManager tracks metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = AsyncMock(spec=YahooDataProvider)

            sample_data = {
                "timestamp": pd.date_range("2024-01-01", periods=5),
                "open": [100.0 + i for i in range(5)],
                "high": [101.0 + i for i in range(5)],
                "low": [99.0 + i for i in range(5)],
                "close": [100.5 + i for i in range(5)],
                "volume": [1000000 + i * 100000 for i in range(5)],
            }
            sample_df = pd.DataFrame(sample_data)
            provider.get_bars.return_value = sample_df

            manager = DataManager(
                provider=provider,
                cache_dir=Path(tmpdir),
            )

            # Get data twice
            await manager.get_data("AAPL", "5m")
            await manager.get_data("AAPL", "5m")

            metrics = manager.get_metrics()

            # First call should fetch, second should hit cache
            assert metrics["total_fetches"] == 1
            assert metrics["cache_hits"] == 1

    @pytest.mark.asyncio
    async def test_merged_data_with_real_time(self):
        """DataManager can merge historical and real-time data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = AsyncMock(spec=YahooDataProvider)

            sample_data = {
                "timestamp": pd.date_range("2024-01-01", periods=3),
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1000000, 1100000, 1200000],
            }
            sample_df = pd.DataFrame(sample_data)
            provider.get_bars.return_value = sample_df

            manager = DataManager(
                provider=provider,
                cache_dir=Path(tmpdir),
            )

            # Add real-time bar with new timestamp (after historical data)
            rt_bar = {
                "timestamp": datetime(2024, 1, 4),  # New timestamp after all historical
                "open": 103.0,
                "high": 104.0,
                "low": 102.0,
                "close": 103.5,
                "volume": 1300000,
            }

            await manager.add_real_time_bar("AAPL", "5m", rt_bar)

            # Get merged data
            merged = await manager.get_merged_data("AAPL", "5m")

            # Should have 3 historical + 1 real-time = 4 rows
            assert len(merged) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
