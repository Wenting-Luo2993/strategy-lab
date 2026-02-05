"""
Market hours logic for NYSE trading hours.
"""

from datetime import datetime
import pytz


def is_market_open(dt: datetime, tz: str = "US/Eastern") -> bool:
    """
    Check if market is open at the given datetime.

    NYSE regular trading hours: 9:30 AM - 4:00 PM ET, Monday-Friday

    Args:
        dt: Datetime to check (should be timezone-aware or will be assumed ET)
        tz: Timezone string (default "US/Eastern")

    Returns:
        True if market is open, False otherwise
    """
    # Convert to ET if not already
    et_tz = pytz.timezone(tz)
    if dt.tzinfo is None:
        dt = et_tz.localize(dt)
    else:
        dt = dt.astimezone(et_tz)

    # Check if it's a weekday (Monday=0, Sunday=6)
    if dt.weekday() >= 5:  # Saturday or Sunday
        return False

    # Check if it's within trading hours (9:30 AM - 4:00 PM)
    market_open_hour = 9
    market_open_minute = 30
    market_close_hour = 16  # 4:00 PM in 24-hour format
    market_close_minute = 0

    current_time = dt.time()
    open_time = current_time.replace(hour=market_open_hour, minute=market_open_minute, second=0, microsecond=0)
    close_time = current_time.replace(hour=market_close_hour, minute=market_close_minute, second=0, microsecond=0)

    return open_time <= current_time < close_time
