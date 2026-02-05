"""
Clock interfaces for time management.
"""

from .base import Clock
from .live_clock import LiveClock
from .market_hours import is_market_open

__all__ = [
    "Clock",
    "LiveClock",
    "is_market_open",
]
