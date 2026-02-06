"""
Market hours logic for NYSE trading hours.

NYSE regular trading hours: 9:30 AM - 4:00 PM ET, Monday-Friday (excluding holidays).

Note: This implementation checks weekdays and trading hours but does NOT handle
US market holidays (Thanksgiving, Christmas, etc.). For production use with holiday
handling, maintain a separate holiday list or integrate with a market calendar library.
"""

from datetime import datetime, time
import pytz


# Define market hours constants
MARKET_OPEN_TIME = time(9, 30, 0)  # 9:30 AM
MARKET_CLOSE_TIME = time(16, 0, 0)  # 4:00 PM (16:00)


def is_market_open(dt: datetime, tz: str = "US/Eastern") -> bool:
    """
    Check if market is open at the given datetime.

    NYSE regular trading hours: 9:30 AM - 4:00 PM ET, Monday-Friday

    This function checks if a given datetime falls within regular market hours.
    It handles timezone conversion automatically.

    IMPORTANT LIMITATION: This function does NOT account for US market holidays
    (e.g., Thanksgiving, Christmas, Independence Day, etc.). For production systems
    requiring accurate holiday handling, use a dedicated market calendar library
    such as pandas_market_calendars or maintain an explicit holiday list.

    Args:
        dt: Datetime to check. Can be timezone-aware or naive (will assume ET if naive).
        tz: Timezone string (default "US/Eastern"). Ignored if dt is already timezone-aware.

    Returns:
        True if market is open (weekday 9:30 AM - 4:00 PM ET), False otherwise.

    Examples:
        >>> from datetime import datetime
        >>> # Check if market is open on Monday at 10:30 AM ET
        >>> is_market_open(datetime(2025, 2, 3, 10, 30))  # Returns True
        >>> # Check if market is open on Saturday
        >>> is_market_open(datetime(2025, 2, 8, 10, 30))  # Returns False
    """
    # Convert to ET if not already timezone-aware
    et_tz = pytz.timezone(tz)
    if dt.tzinfo is None:
        # Naive datetime: localize to the specified timezone
        dt = et_tz.localize(dt)
    else:
        # Timezone-aware datetime: convert to the specified timezone
        dt = dt.astimezone(et_tz)

    # Check if it's a weekday (Monday=0, Sunday=6)
    # Market is closed on Saturday (5) and Sunday (6)
    if dt.weekday() >= 5:
        return False

    # Check if time is within market hours (inclusive of open, exclusive of close)
    # 9:30 AM <= time < 4:00 PM
    current_time = dt.time()
    return MARKET_OPEN_TIME <= current_time < MARKET_CLOSE_TIME
