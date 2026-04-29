import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from vibe.backtester.core.clock import SimulatedClock

ET = ZoneInfo("America/New_York")

def test_raises_before_set():
    clock = SimulatedClock()
    with pytest.raises(RuntimeError, match="set_time"):
        clock.now()

def test_now_returns_set_time():
    clock = SimulatedClock()
    ts = datetime(2024, 1, 15, 10, 0, tzinfo=ET)
    clock.set_time(ts)
    assert clock.now() == ts

def test_is_market_open_during_hours():
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 10, 0, tzinfo=ET))
    assert clock.is_market_open() is True

def test_is_market_open_before_open():
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 9, 0, tzinfo=ET))
    assert clock.is_market_open() is False

def test_is_market_open_at_close():
    clock = SimulatedClock()
    clock.set_time(datetime(2024, 1, 15, 16, 0, tzinfo=ET))
    assert clock.is_market_open() is False

def test_is_market_open_utc_input():
    """UTC timestamp should be converted correctly."""
    from datetime import timezone
    clock = SimulatedClock()
    # 14:30 UTC = 10:30 ET (during summer)
    clock.set_time(datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc))
    assert clock.is_market_open() is True
