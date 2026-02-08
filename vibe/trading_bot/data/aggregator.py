"""
Bar aggregator for converting trades to OHLCV bars.
"""

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd
import pytz


logger = logging.getLogger(__name__)


class Bar:
    """Represents an OHLCV bar during aggregation."""

    def __init__(self, timestamp: datetime):
        """
        Initialize bar.

        Args:
            timestamp: Bar open time
        """
        self.timestamp = timestamp
        self.open: Optional[float] = None
        self.high: float = 0
        self.low: float = float('inf')
        self.close: float = 0
        self.volume: float = 0
        self.trade_count: int = 0

    def add_trade(self, price: float, size: float) -> None:
        """
        Add a trade to the bar.

        Args:
            price: Trade price
            size: Trade size/volume
        """
        if self.open is None:
            self.open = price

        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size
        self.trade_count += 1

    def to_dict(self) -> dict:
        """Convert bar to dictionary."""
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
        }

    def to_series(self) -> pd.Series:
        """Convert bar to pandas Series."""
        return pd.Series({
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
        })


class BarAggregator:
    """
    Aggregates trades into OHLCV bars with configurable intervals.

    Handles timezone-aware bar boundaries and late trades.
    """

    # Supported intervals with their duration in seconds
    INTERVAL_SECONDS = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    def __init__(
        self,
        bar_interval: str = "5m",
        timezone: str = "US/Eastern",
        late_trade_handling: str = "previous",  # "previous" or "synthetic"
    ):
        """
        Initialize bar aggregator.

        Args:
            bar_interval: Bar interval ('1m', '5m', '15m', '1h', etc.)
            timezone: Timezone for bar boundaries (e.g., 'US/Eastern')
            late_trade_handling: How to handle trades after bar close
                               ('previous' = update previous bar, 'synthetic' = new bar)
        """
        if bar_interval not in self.INTERVAL_SECONDS:
            raise ValueError(
                f"Invalid interval '{bar_interval}'. "
                f"Valid: {list(self.INTERVAL_SECONDS.keys())}"
            )

        self.bar_interval = bar_interval
        self.interval_seconds = self.INTERVAL_SECONDS[bar_interval]
        self.timezone = pytz.timezone(timezone)
        self.late_trade_handling = late_trade_handling

        # Current bar being built
        self.current_bar: Optional[Bar] = None
        self.current_bar_start_time: Optional[datetime] = None

        # Bar completion callbacks
        self._on_bar_complete: Optional[Callable[[dict], None]] = None

        # Track late trades
        self.late_trades_count = 0
        self.previous_bar: Optional[Bar] = None

    def on_bar_complete(self, callback: Callable[[dict], None]) -> None:
        """
        Register callback for bar completion.

        Args:
            callback: Function called with bar dict when bar completes
        """
        self._on_bar_complete = callback

    def _get_bar_start_time(self, timestamp: datetime) -> datetime:
        """
        Get the start time of the bar for a given timestamp.

        Aligns to interval boundaries (e.g., 5m bars start at :00, :05, :10, etc.)

        Args:
            timestamp: Trade timestamp

        Returns:
            Bar start time aligned to interval
        """
        # Ensure timestamp is in the configured timezone
        if timestamp.tzinfo is None:
            timestamp = self.timezone.localize(timestamp)
        else:
            timestamp = timestamp.astimezone(self.timezone)

        # Calculate seconds since the start of the day
        seconds_since_midnight = (
            timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
        )

        # Calculate which bar this belongs to
        bar_index = seconds_since_midnight // self.interval_seconds

        # Calculate the start of this bar
        bar_start_seconds = bar_index * self.interval_seconds

        # Create the bar start datetime
        bar_start = timestamp.replace(
            hour=bar_start_seconds // 3600,
            minute=(bar_start_seconds % 3600) // 60,
            second=bar_start_seconds % 60,
            microsecond=0,
        )

        return bar_start

    def add_trade(
        self,
        timestamp: datetime,
        price: float,
        size: float,
    ) -> Optional[dict]:
        """
        Add a trade to the aggregator.

        Returns completed bars when crossing boundaries.

        Args:
            timestamp: Trade timestamp
            price: Trade price
            size: Trade size/volume

        Returns:
            Completed bar dict if bar completed, None otherwise
        """
        if price <= 0 or size <= 0:
            logger.warning(f"Invalid trade data: price={price}, size={size}")
            return None

        # Get the bar this trade belongs to
        trade_bar_start = self._get_bar_start_time(timestamp)

        # Initialize current bar if needed
        if self.current_bar is None:
            self.current_bar = Bar(trade_bar_start)
            self.current_bar_start_time = trade_bar_start

        # Check if trade belongs to current bar
        if trade_bar_start == self.current_bar_start_time:
            # Add trade to current bar
            self.current_bar.add_trade(price, size)
            return None

        else:
            # Trade belongs to a different bar
            if trade_bar_start < self.current_bar_start_time:
                # Late trade for a previous bar
                self.late_trades_count += 1
                logger.debug(
                    f"Late trade detected: trade time {timestamp} "
                    f"< bar start {self.current_bar_start_time}"
                )

                if self.late_trade_handling == "previous":
                    # Update previous bar if we have it
                    if self.previous_bar:
                        self.previous_bar.add_trade(price, size)
                else:
                    # Create synthetic bar for this trade
                    synthetic_bar = Bar(trade_bar_start)
                    synthetic_bar.add_trade(price, size)
                    self.previous_bar = synthetic_bar

                return None

            else:
                # Trade belongs to a future bar - complete current bar
                completed_bar = self.current_bar.to_dict()

                # Store completed bar for reference
                self.previous_bar = self.current_bar

                # Start new bar
                self.current_bar = Bar(trade_bar_start)
                self.current_bar_start_time = trade_bar_start

                # Add trade to new bar
                self.current_bar.add_trade(price, size)

                # Call completion callback
                if self._on_bar_complete:
                    self._on_bar_complete(completed_bar)

                return completed_bar

    def flush(self) -> Optional[dict]:
        """
        Flush the current bar.

        Useful at end of trading day or when stopping aggregation.

        Returns:
            Completed bar dict if there's a current bar, None otherwise
        """
        if self.current_bar is None or self.current_bar.trade_count == 0:
            return None

        completed_bar = self.current_bar.to_dict()
        self.previous_bar = self.current_bar
        self.current_bar = None
        self.current_bar_start_time = None

        if self._on_bar_complete:
            self._on_bar_complete(completed_bar)

        return completed_bar

    def get_stats(self) -> dict:
        """
        Get aggregator statistics.

        Returns:
            Dictionary with statistics
        """
        return {
            "interval": self.bar_interval,
            "current_bar_start": self.current_bar_start_time.isoformat()
            if self.current_bar_start_time
            else None,
            "current_bar_trades": self.current_bar.trade_count if self.current_bar else 0,
            "late_trades_count": self.late_trades_count,
            "timezone": str(self.timezone),
        }

    def reset(self) -> None:
        """Reset aggregator state."""
        self.current_bar = None
        self.current_bar_start_time = None
        self.previous_bar = None
        self.late_trades_count = 0

    @staticmethod
    def create_bars_dataframe(bars: List[dict]) -> pd.DataFrame:
        """
        Create a DataFrame from a list of bar dictionaries.

        Args:
            bars: List of bar dicts

        Returns:
            DataFrame with OHLCV data
        """
        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df = df.set_index("timestamp")
        return df
