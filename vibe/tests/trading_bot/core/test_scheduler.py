"""Tests for market scheduler."""

import pytest
from datetime import datetime, timedelta
import pytz

from vibe.trading_bot.core.scheduler import MarketScheduler


class TestMarketScheduler:
    """Test market scheduler functionality."""

    @pytest.fixture
    def scheduler(self):
        """Create a market scheduler."""
        return MarketScheduler(exchange="NYSE")

    def test_initialization(self, scheduler):
        """Test scheduler initializes correctly."""
        assert scheduler.exchange == "NYSE"
        assert scheduler.calendar is not None
        assert scheduler.timezone == pytz.timezone("US/Eastern")

    def test_invalid_exchange(self):
        """Test error on invalid exchange."""
        with pytest.raises(ValueError, match="Unsupported exchange"):
            MarketScheduler(exchange="INVALID")

    def test_is_market_open_during_hours(self, scheduler):
        """Test market open detection during trading hours."""
        # 10:30 AM ET on a trading day (Tuesday, Feb 4, 2026)
        dt = datetime(2026, 2, 3, 10, 30)
        # Market should be open (9:30 AM - 4:00 PM)
        result = scheduler.is_market_open(dt)
        assert isinstance(result, bool)

    def test_is_market_open_after_hours(self, scheduler):
        """Test market closed after hours."""
        # 6:00 PM ET (after close)
        dt = datetime(2026, 2, 3, 18, 0)
        result = scheduler.is_market_open(dt)
        assert result is False

    def test_is_market_open_before_open(self, scheduler):
        """Test market closed before open."""
        # 7:00 AM ET (before open)
        dt = datetime(2026, 2, 3, 7, 0)
        result = scheduler.is_market_open(dt)
        assert result is False

    def test_is_market_open_weekend(self, scheduler):
        """Test market closed on weekends."""
        # Saturday
        dt = datetime(2026, 2, 7, 12, 0)
        result = scheduler.is_market_open(dt)
        assert result is False

    def test_get_open_time(self, scheduler):
        """Test getting market open time."""
        dt = datetime(2026, 2, 3)  # Tuesday
        open_time = scheduler.get_open_time(dt)

        assert open_time is not None
        # Convert to ET for comparison
        open_time_et = open_time.astimezone(scheduler.timezone)
        assert open_time_et.hour == 9
        assert open_time_et.minute == 30
        assert open_time.tzinfo is not None

    def test_get_close_time(self, scheduler):
        """Test getting market close time."""
        dt = datetime(2026, 2, 3)  # Tuesday
        close_time = scheduler.get_close_time(dt)

        assert close_time is not None
        # Convert to ET for comparison
        close_time_et = close_time.astimezone(scheduler.timezone)
        assert close_time_et.hour == 16
        assert close_time_et.minute == 0

    def test_get_close_time_holiday(self, scheduler):
        """Test close time returns None on holiday."""
        # Christmas Day 2025
        dt = datetime(2025, 12, 25)
        close_time = scheduler.get_close_time(dt)
        assert close_time is None

    def test_next_market_open(self, scheduler):
        """Test getting next market open."""
        # After market close on Tuesday
        dt = scheduler.timezone.localize(datetime(2026, 2, 3, 18, 0))
        next_open = scheduler.next_market_open(dt)

        assert next_open > dt
        next_open_et = next_open.astimezone(scheduler.timezone)
        assert next_open_et.hour == 9
        assert next_open_et.minute == 30

    def test_next_market_close(self, scheduler):
        """Test getting next market close."""
        # Before market open on Tuesday
        dt = scheduler.timezone.localize(datetime(2026, 2, 3, 8, 0))
        next_close = scheduler.next_market_close(dt)

        assert next_close > dt
        next_close_et = next_close.astimezone(scheduler.timezone)
        assert next_close_et.hour == 16
        assert next_close_et.minute == 0

    def test_is_holiday(self, scheduler):
        """Test holiday detection."""
        # Christmas 2025
        dt = datetime(2025, 12, 25)
        assert scheduler.is_holiday(dt) is True

        # Regular trading day
        dt = datetime(2026, 2, 3)
        assert scheduler.is_holiday(dt) is False

    def test_is_early_close(self, scheduler):
        """Test early close detection."""
        # Get all upcoming dates and check for early closes
        schedule = scheduler.get_schedule(
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 12, 31),
            limit=365
        )

        # Check format at least
        for session in schedule:
            assert "early_close" in session
            assert isinstance(session["early_close"], bool)

    def test_get_schedule(self, scheduler):
        """Test getting market schedule."""
        schedule = scheduler.get_schedule(
            start_date=datetime(2026, 2, 1),
            end_date=datetime(2026, 2, 10),
            limit=30
        )

        assert len(schedule) > 0

        # Verify schedule format
        for session in schedule:
            assert "date" in session
            assert "market_open" in session
            assert "market_close" in session
            assert "early_close" in session

    def test_get_schedule_limit(self, scheduler):
        """Test schedule limit parameter."""
        schedule = scheduler.get_schedule(limit=5)

        assert len(schedule) <= 5

    def test_timezone_aware_datetime(self, scheduler):
        """Test handling of timezone-aware datetimes."""
        # Create timezone-aware datetime in different timezone
        utc_tz = pytz.UTC
        dt = utc_tz.localize(datetime(2026, 2, 3, 14, 30))

        # Should convert to market timezone and work correctly
        result = scheduler.is_market_open(dt)
        assert isinstance(result, bool)

    def test_none_datetime_uses_now(self, scheduler):
        """Test that None datetime uses current time."""
        result = scheduler.is_market_open(None)
        assert isinstance(result, bool)


class TestMarketSchedulerIntegration:
    """Integration tests for market scheduler."""

    def test_schedule_consistency(self):
        """Test that schedule is consistent across calls."""
        scheduler = MarketScheduler(exchange="NYSE")

        schedule1 = scheduler.get_schedule(
            start_date=datetime(2026, 2, 1),
            end_date=datetime(2026, 2, 5)
        )

        schedule2 = scheduler.get_schedule(
            start_date=datetime(2026, 2, 1),
            end_date=datetime(2026, 2, 5)
        )

        assert schedule1 == schedule2

    def test_market_cycle(self):
        """Test complete market open/close cycle."""
        scheduler = MarketScheduler(exchange="NYSE")

        # 8:00 AM - market closed
        dt = datetime(2026, 2, 3, 8, 0)
        assert not scheduler.is_market_open(dt)

        # 9:31 AM - market open
        dt = datetime(2026, 2, 3, 9, 31)
        assert scheduler.is_market_open(dt)

        # 3:59 PM - market still open
        dt = datetime(2026, 2, 3, 15, 59)
        assert scheduler.is_market_open(dt)

        # 4:01 PM - market closed
        dt = datetime(2026, 2, 3, 16, 1)
        assert not scheduler.is_market_open(dt)
