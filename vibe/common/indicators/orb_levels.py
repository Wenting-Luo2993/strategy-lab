"""
Opening Range Breakout (ORB) Level Calculator.

Calculates ORB_High, ORB_Low, and ORB_Range from the opening window of the trading day.
Supports configurable opening window, body percentage filter, and partial market days.
"""

import logging
from datetime import datetime, time
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ORBLevels:
    """ORB levels for a trading day."""

    high: float
    low: float
    range: float
    valid: bool = True
    reason: str = ""


class ORBCalculator:
    """
    Calculates Opening Range Breakout levels from intraday data.

    Supports:
    - Configurable opening window (e.g., 9:30-9:35)
    - Body percentage filter (>50% body = valid breakout candle)
    - Partial market days (market open, early close)
    - Daily level reset at market open
    """

    def __init__(
        self,
        start_time: str = "09:30",
        duration_minutes: int = 5,
        body_pct_filter: float = 0.5,
        market_open: str = "09:30",
        market_close: str = "16:00",
    ):
        """
        Initialize ORB Calculator.

        Args:
            start_time: Opening window start time (HH:MM format)
            duration_minutes: Duration of opening window in minutes
            body_pct_filter: Minimum body percentage for valid breakout candle (0.0-1.0)
            market_open: Market open time (HH:MM format)
            market_close: Market close time (HH:MM format)
        """
        self.start_time = self._parse_time(start_time)
        self.duration_minutes = duration_minutes
        self.body_pct_filter = body_pct_filter
        self.market_open = self._parse_time(market_open)
        self.market_close = self._parse_time(market_close)

        # Cache for current day's levels
        self._current_date: Optional[str] = None
        self._current_levels: Optional[ORBLevels] = None

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse time string (HH:MM) to time object."""
        hour, minute = map(int, time_str.split(":"))
        return time(hour, minute)

    def _get_time_from_timestamp(self, ts: datetime) -> time:
        """Extract time from datetime."""
        return ts.time()

    def _is_in_opening_window(self, ts: datetime) -> bool:
        """Check if timestamp is within opening window."""
        bar_time = self._get_time_from_timestamp(ts)

        # Calculate end time
        start_minutes = self.start_time.hour * 60 + self.start_time.minute
        end_minutes = start_minutes + self.duration_minutes
        end_time = time(end_minutes // 60, end_minutes % 60)

        return self.start_time <= bar_time < end_time

    def _calculate_body_percentage(self, open_: float, close: float, high: float, low: float) -> float:
        """
        Calculate body percentage of a candle.

        Body percentage = body_size / total_range
        Body size = |close - open|
        Total range = high - low
        """
        total_range = high - low
        if total_range == 0:
            return 0.0

        body_size = abs(close - open_)
        return body_size / total_range

    def _is_valid_breakout_candle(self, open_: float, close: float, high: float, low: float) -> bool:
        """Check if candle has sufficient body percentage."""
        body_pct = self._calculate_body_percentage(open_, close, high, low)
        return body_pct >= self.body_pct_filter

    def calculate(self, df: pd.DataFrame) -> ORBLevels:
        """
        Calculate ORB levels from DataFrame.

        Args:
            df: DataFrame with columns: timestamp, open, high, low, close, volume
                timestamp should be datetime

        Returns:
            ORBLevels object with high, low, range, and validity
        """
        if df.empty:
            return ORBLevels(
                high=0.0,
                low=0.0,
                range=0.0,
                valid=False,
                reason="Empty dataframe",
            )

        # Check if data is for new day - use FIRST bar's date, not last
        first_date = df.iloc[0]["timestamp"].date()
        first_date_str = str(first_date)

        # If we have cached levels for a different day, invalidate
        if self._current_date != first_date_str:
            self._current_date = None
            self._current_levels = None

        # Check cache
        if self._current_date == first_date_str and self._current_levels:
            return self._current_levels

        # Filter bars in opening window
        opening_bars = []
        for idx, row in df.iterrows():
            if self._is_in_opening_window(row["timestamp"]):
                opening_bars.append(row)
            elif len(opening_bars) > 0:
                # Stop once we exit the opening window
                break

        if not opening_bars:
            return ORBLevels(
                high=0.0,
                low=0.0,
                range=0.0,
                valid=False,
                reason="No bars in opening window",
            )

        # Calculate ORB levels
        highs = [bar["high"] for bar in opening_bars]
        lows = [bar["low"] for bar in opening_bars]

        orb_high = max(highs)
        orb_low = min(lows)
        orb_range = orb_high - orb_low

        # Check if first candle is a valid breakout candle
        first_bar = opening_bars[0]
        valid = self._is_valid_breakout_candle(
            first_bar["open"],
            first_bar["close"],
            first_bar["high"],
            first_bar["low"],
        )

        reason = ""
        if not valid:
            body_pct = self._calculate_body_percentage(
                first_bar["open"],
                first_bar["close"],
                first_bar["high"],
                first_bar["low"],
            )
            reason = f"First candle body {body_pct:.1%} < {self.body_pct_filter:.1%} threshold"

        levels = ORBLevels(
            high=orb_high,
            low=orb_low,
            range=orb_range,
            valid=valid,
            reason=reason,
        )

        # Cache for current day
        self._current_date = first_date_str
        self._current_levels = levels

        return levels

    def is_long_breakout(self, current_price: float, levels: ORBLevels) -> bool:
        """
        Check if price has broken above ORB_High.

        Args:
            current_price: Current price
            levels: ORBLevels from calculate()

        Returns:
            True if price >= ORB_High (inclusive)
        """
        return current_price >= levels.high

    def is_short_breakout(self, current_price: float, levels: ORBLevels) -> bool:
        """
        Check if price has broken below ORB_Low.

        Args:
            current_price: Current price
            levels: ORBLevels from calculate()

        Returns:
            True if price <= ORB_Low (inclusive)
        """
        return current_price <= levels.low

    def get_long_exit_level(self, levels: ORBLevels, atr: float, multiplier: float = 2.0) -> float:
        """
        Calculate long take-profit level.

        Args:
            levels: ORBLevels from calculate()
            atr: Average True Range
            multiplier: ATR multiplier for profit target (default 2.0)

        Returns:
            Take-profit price
        """
        return levels.high + (atr * multiplier)

    def get_short_exit_level(self, levels: ORBLevels, atr: float, multiplier: float = 2.0) -> float:
        """
        Calculate short take-profit level.

        Args:
            levels: ORBLevels from calculate()
            atr: Average True Range
            multiplier: ATR multiplier for profit target (default 2.0)

        Returns:
            Take-profit price
        """
        return levels.low - (atr * multiplier)

    def reset_cache(self) -> None:
        """Reset daily cache."""
        self._current_date = None
        self._current_levels = None
