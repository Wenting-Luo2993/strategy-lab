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
        """Extract time from datetime in Eastern (market) timezone.

        CRITICAL: Always convert to Eastern timezone before extracting time.
        Timestamps may be UTC (from yfinance/Finnhub), and comparing UTC times
        directly to market-hour boundaries (e.g., 09:30) would be wrong.
        """
        import pytz
        market_tz = pytz.timezone("America/New_York")
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            ts = ts.astimezone(market_tz)
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

    def calculate(self, df: pd.DataFrame, trading_date: Optional[datetime] = None) -> ORBLevels:
        """
        Calculate ORB levels from DataFrame.

        Args:
            df: DataFrame with columns: timestamp, open, high, low, close, volume
                timestamp should be datetime
            trading_date: Current trading date (optional). If provided, only bars from this
                         date will be used for ORB calculation. If not provided, will use
                         the last bar's date.

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

        # Defensive check: Ensure timestamp column is datetime
        if "timestamp" not in df.columns:
            return ORBLevels(
                high=0.0,
                low=0.0,
                range=0.0,
                valid=False,
                reason="Missing timestamp column",
            )

        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            # Try to convert to datetime
            logger.warning(
                f"ORB Calculator: timestamp column is {df['timestamp'].dtype}, attempting to convert to datetime"
            )
            try:
                df = df.copy()
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            except Exception as e:
                logger.error(f"ORB Calculator: Failed to convert timestamp to datetime: {e}")
                return ORBLevels(
                    high=0.0,
                    low=0.0,
                    range=0.0,
                    valid=False,
                    reason=f"Invalid timestamp dtype: {df['timestamp'].dtype}",
                )

        # Determine current trading date
        # CRITICAL: Always compute the trading date in Eastern (market) timezone.
        # df["timestamp"].dt.date returns dates in the timezone of stored timestamps.
        # yfinance and BarAggregator both store timestamps in America/New_York (Eastern).
        # If trading_date is in UTC (e.g., from Finnhub bars), trading_date.date() returns
        # a UTC date which may differ from the Eastern date stored in df["timestamp"].dt.date
        # when the DataFrame timestamps did NOT get converted to UTC by pd.concat.
        # By converting trading_date to Eastern before extracting .date(), we ensure
        # current_date always uses the same timezone as df["timestamp"].dt.date.
        import pytz
        _market_tz = pytz.timezone("America/New_York")
        if trading_date is not None:
            # Convert to Eastern timezone before extracting the date
            if hasattr(trading_date, 'tzinfo') and trading_date.tzinfo is not None:
                trading_date_local = trading_date.astimezone(_market_tz)
            elif hasattr(trading_date, 'tzinfo') and trading_date.tzinfo is None:
                # Naive datetime - assume UTC for safety
                trading_date_local = pytz.utc.localize(trading_date).astimezone(_market_tz)
            else:
                trading_date_local = trading_date
            current_date = trading_date_local.date() if hasattr(trading_date_local, 'date') else trading_date_local
        else:
            # Fall back to inferring from DataFrame (use last bar's date in Eastern tz)
            last_ts = df.iloc[-1]["timestamp"]
            if hasattr(last_ts, 'tzinfo') and last_ts.tzinfo is not None:
                last_ts = last_ts.astimezone(_market_tz)
            current_date = last_ts.date()

        current_date_str = str(current_date)

        # If we have cached levels for a different day, invalidate
        if self._current_date != current_date_str:
            self._current_date = None
            self._current_levels = None

        # Check cache
        if self._current_date == current_date_str and self._current_levels:
            logger.info(
                f"ORB Calculate: cache HIT for {current_date_str}, "
                f"returning cached high=${self._current_levels.high:.2f}, low=${self._current_levels.low:.2f}"
            )
            return self._current_levels

        # Filter bars from current trading day only, then find bars in opening window.
        # CRITICAL: Normalize timestamps to Eastern timezone before comparing dates.
        # Timestamps in df may be in any timezone (UTC from Finnhub, Eastern from yfinance,
        # or object dtype with mixed timezones). Computing dt.date on UTC timestamps returns
        # UTC dates, while current_date is in Eastern. For market hours bars these dates are
        # the same calendar day, but to be safe we always compare in Eastern timezone.
        try:
            ts_dtype = df["timestamp"].dtype
            ts_is_tz_aware = (
                hasattr(ts_dtype, 'tz') and ts_dtype.tz is not None
            )
            if ts_is_tz_aware:
                # tz-aware datetime64: convert to Eastern and extract date
                ts_eastern = df["timestamp"].dt.tz_convert(_market_tz)
                current_day_df = df[ts_eastern.dt.date == current_date].copy()
            else:
                # tz-naive datetime64 or object dtype: fall back to direct comparison
                # (object dtype was already caught by the DTYPE FIX earlier in this method)
                current_day_df = df[df["timestamp"].dt.date == current_date].copy()
        except Exception as e:
            logger.warning(f"ORB Calculate: date filter failed ({e}), falling back to direct comparison")
            current_day_df = df[df["timestamp"].dt.date == current_date].copy()

        # DEBUG: Log filtering details
        logger.info(
            f"ORB Calculate: trading_date={current_date}, total_bars={len(df)}, "
            f"current_day_bars={len(current_day_df)}"
        )
        if not current_day_df.empty:
            logger.info(
                f"ORB Calculate: current_day bars timestamps: "
                f"{current_day_df['timestamp'].tolist()}"
            )

        if current_day_df.empty:
            # DEBUG: Show why no bars matched
            if not df.empty:
                bar_dates = df['timestamp'].dt.date.unique()
                logger.warning(
                    f"ORB Calculate: No bars for {current_date}. "
                    f"Available dates in DataFrame: {bar_dates.tolist()}"
                )
            return ORBLevels(
                high=0.0,
                low=0.0,
                range=0.0,
                valid=False,
                reason=f"No bars for current trading day ({current_date})",
            )

        # Filter bars in opening window
        opening_bars = []
        for idx, row in current_day_df.iterrows():
            if self._is_in_opening_window(row["timestamp"]):
                opening_bars.append(row)
            elif len(opening_bars) > 0:
                # Stop once we exit the opening window
                break

        # DEBUG: Log opening window filtering
        logger.info(
            f"ORB Calculate: opening_window={self.start_time}-{self.start_time.hour}:{self.start_time.minute + self.duration_minutes:02d}, "
            f"bars_in_window={len(opening_bars)}"
        )
        if opening_bars:
            opening_timestamps = [row['timestamp'] for row in opening_bars]
            logger.info(f"ORB Calculate: opening_bars timestamps: {opening_timestamps}")

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

        logger.info(
            f"ORB Calculate: RESULT high=${orb_high:.2f}, low=${orb_low:.2f}, "
            f"range=${orb_range:.2f} (from {len(opening_bars)} opening bar(s))"
        )

        # ORB levels are always valid if we have bars in the opening window
        # Body percentage filter should be applied to BREAKOUT bars, not ORB bars
        # (The ORB bar just establishes the range - body size is irrelevant)
        levels = ORBLevels(
            high=orb_high,
            low=orb_low,
            range=orb_range,
            valid=True,  # Always valid if bars exist in opening window
            reason="",
        )

        # Cache for current day
        self._current_date = current_date_str
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
