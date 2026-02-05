"""Tests for Task 0.5: Abstract Clock Interface."""

import pytest
from datetime import datetime
import pytz

from vibe.common.clock import Clock, LiveClock, is_market_open


def test_clock_abstract():
    """Clock cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Clock()


def test_clock_has_required_methods():
    """Clock has all required abstract methods."""
    required_methods = ["now", "is_market_open"]

    for method_name in required_methods:
        assert hasattr(Clock, method_name)


class ConcreteClock(Clock):
    """Concrete implementation of Clock for testing."""

    def __init__(self, test_time):
        self.test_time = test_time

    def now(self):
        """Return test time."""
        return self.test_time

    def is_market_open(self):
        """Return True if within market hours."""
        return is_market_open(self.test_time)


def test_clock_concrete_implementation():
    """Clock can be subclassed and instantiated."""
    test_time = datetime.now()
    clock = ConcreteClock(test_time)
    assert clock is not None
    assert clock.now() == test_time


def test_live_clock_instantiation():
    """LiveClock can be instantiated."""
    clock = LiveClock()
    assert clock is not None


def test_live_clock_now():
    """LiveClock returns current time."""
    clock = LiveClock()
    now = clock.now()

    # Verify it's a datetime
    assert isinstance(now, datetime)

    # Verify it's close to actual current time (within 1 second)
    delta = abs((datetime.now() - now).total_seconds())
    assert delta < 1


def test_live_clock_is_market_open():
    """LiveClock can check if market is open."""
    clock = LiveClock()
    is_open = clock.is_market_open()

    # Verify it returns a boolean
    assert isinstance(is_open, bool)


class TestMarketHours:
    """Tests for market hours function."""

    def test_market_open_monday_morning(self):
        """Market is open Monday morning 10:30 AM ET."""
        # Monday, Feb 3, 2025, 10:30 AM ET
        dt = datetime(2025, 2, 3, 10, 30)
        assert is_market_open(dt, tz="US/Eastern") is True

    def test_market_closed_before_open(self):
        """Market is closed before 9:30 AM."""
        # Monday, Feb 3, 2025, 9:00 AM ET
        dt = datetime(2025, 2, 3, 9, 0)
        assert is_market_open(dt, tz="US/Eastern") is False

    def test_market_closed_after_close(self):
        """Market is closed after 4:00 PM."""
        # Monday, Feb 3, 2025, 5:00 PM ET
        dt = datetime(2025, 2, 3, 17, 0)
        assert is_market_open(dt, tz="US/Eastern") is False

    def test_market_closed_at_exact_close(self):
        """Market is closed at 4:00 PM (close time)."""
        # Monday, Feb 3, 2025, 4:00 PM ET
        dt = datetime(2025, 2, 3, 16, 0)
        assert is_market_open(dt, tz="US/Eastern") is False

    def test_market_open_at_exact_open(self):
        """Market is open at 9:30 AM (open time)."""
        # Monday, Feb 3, 2025, 9:30 AM ET
        dt = datetime(2025, 2, 3, 9, 30)
        assert is_market_open(dt, tz="US/Eastern") is True

    def test_market_closed_saturday(self):
        """Market is closed on Saturday."""
        # Saturday, Feb 8, 2025, 10:30 AM ET
        dt = datetime(2025, 2, 8, 10, 30)
        assert is_market_open(dt, tz="US/Eastern") is False

    def test_market_closed_sunday(self):
        """Market is closed on Sunday."""
        # Sunday, Feb 9, 2025, 10:30 AM ET
        dt = datetime(2025, 2, 9, 10, 30)
        assert is_market_open(dt, tz="US/Eastern") is False

    def test_market_closed_friday_after_close(self):
        """Market is closed Friday after 4:00 PM."""
        # Friday, Feb 7, 2025, 5:00 PM ET
        dt = datetime(2025, 2, 7, 17, 0)
        assert is_market_open(dt, tz="US/Eastern") is False

    def test_market_open_with_timezone_aware_datetime(self):
        """Market check works with timezone-aware datetime."""
        et_tz = pytz.timezone("US/Eastern")
        dt = et_tz.localize(datetime(2025, 2, 3, 10, 30))
        assert is_market_open(dt, tz="US/Eastern") is True

    def test_market_hours_afternoon(self):
        """Market is open in the afternoon."""
        # Monday, Feb 3, 2025, 2:30 PM ET
        dt = datetime(2025, 2, 3, 14, 30)
        assert is_market_open(dt, tz="US/Eastern") is True

    def test_market_hours_near_close(self):
        """Market is open near close time."""
        # Monday, Feb 3, 2025, 3:59 PM ET
        dt = datetime(2025, 2, 3, 15, 59)
        assert is_market_open(dt, tz="US/Eastern") is True


class TestLiveClockFunctional:
    """Functional tests for LiveClock."""

    def test_live_clock_time_advances(self):
        """LiveClock returns different times on subsequent calls."""
        import time

        clock = LiveClock()
        time1 = clock.now()
        time.sleep(0.1)
        time2 = clock.now()

        assert time2 > time1
