"""
Multi-Timeframe Data Store for aggregating bars across timeframes.

Efficiently aggregates lower timeframe bars to higher timeframes while
maintaining synchronized data across all timeframes.
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass
import pandas as pd

logger = logging.getLogger(__name__)


# Timeframe to minutes mapping
TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

# Aggregation ratios
AGGREGATION_RATIOS = {
    ("5m", "15m"): 3,
    ("5m", "1h"): 12,
    ("5m", "4h"): 48,
    ("5m", "1d"): 288,
    ("15m", "1h"): 4,
    ("15m", "4h"): 16,
    ("15m", "1d"): 96,
    ("1h", "4h"): 4,
    ("1h", "1d"): 24,
}


@dataclass
class Bar:
    """Represents a candlestick bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @staticmethod
    def from_dict(data: Dict) -> "Bar":
        """Create from dictionary."""
        return Bar(**data)


class MTFDataStore:
    """
    Multi-Timeframe Data Store for managing bars across timeframes.

    Maintains a primary timeframe (typically 5m) as source of truth and
    aggregates to higher timeframes on demand.
    """

    def __init__(
        self,
        primary_tf: str = "5m",
        htf_list: Optional[List[str]] = None,
        max_bars_per_tf: int = 1000,
    ):
        """
        Initialize MTF Data Store.

        Args:
            primary_tf: Primary timeframe (source of truth)
            htf_list: List of higher timeframes to maintain
            max_bars_per_tf: Maximum bars to keep per timeframe (for memory management)
        """
        self.primary_tf = primary_tf
        self.htf_list = htf_list or ["15m", "1h", "4h", "1d"]
        self.max_bars_per_tf = max_bars_per_tf

        # Store bars for each timeframe and symbol
        # Structure: {symbol: {timeframe: deque([Bar, ...])}}
        self.bars: Dict[str, Dict[str, deque]] = {}

        # Track incomplete bars being built
        # Structure: {symbol: {timeframe: Bar}}
        self.incomplete_bars: Dict[str, Dict[str, Optional[Bar]]] = {}

    def add_bar(self, symbol: str, bar: Bar) -> Dict[str, Optional[Bar]]:
        """
        Add a bar at primary timeframe.

        Args:
            symbol: Trading symbol
            bar: Bar at primary timeframe

        Returns:
            Dict mapping timeframe -> completed Bar (or None if not completed)
        """
        if symbol not in self.bars:
            self.bars[symbol] = {self.primary_tf: deque(maxlen=self.max_bars_per_tf)}
            self.incomplete_bars[symbol] = {}

        # Add to primary timeframe
        self.bars[symbol][self.primary_tf].append(bar)

        completed_bars = {tf: None for tf in [self.primary_tf] + self.htf_list}
        completed_bars[self.primary_tf] = bar

        # Aggregate to higher timeframes
        for htf in self.htf_list:
            completed = self._aggregate_to_htf(symbol, bar, htf)
            if completed:
                completed_bars[htf] = completed

        return completed_bars

    def _aggregate_to_htf(self, symbol: str, primary_bar: Bar, htf: str) -> Optional[Bar]:
        """
        Attempt to aggregate primary bar to higher timeframe.

        Returns completed HTF bar if this completes the HTF, None otherwise.
        """
        # Initialize HTF store if needed
        if htf not in self.bars[symbol]:
            self.bars[symbol][htf] = deque(maxlen=self.max_bars_per_tf)

        # Get aggregation ratio
        ratio = self._get_aggregation_ratio(self.primary_tf, htf)
        if ratio is None:
            return None

        # Get or create incomplete bar
        if htf not in self.incomplete_bars[symbol]:
            self.incomplete_bars[symbol][htf] = None

        incomplete = self.incomplete_bars[symbol][htf]

        # Check if this bar should start a new HTF bar
        if incomplete is None or self._bar_starts_new_htf(primary_bar.timestamp, incomplete.timestamp, htf):
            # Complete previous if exists
            if incomplete:
                self.bars[symbol][htf].append(incomplete)

            # Start new HTF bar
            self.incomplete_bars[symbol][htf] = Bar(
                timestamp=self._align_timestamp(primary_bar.timestamp, htf),
                open=primary_bar.open,
                high=primary_bar.high,
                low=primary_bar.low,
                close=primary_bar.close,
                volume=primary_bar.volume,
            )
            return None

        # Update incomplete bar
        incomplete.high = max(incomplete.high, primary_bar.high)
        incomplete.low = min(incomplete.low, primary_bar.low)
        incomplete.close = primary_bar.close
        incomplete.volume += primary_bar.volume

        # Check if HTF bar is complete
        bar_count_in_htf = self._count_bars_in_htf(symbol, incomplete.timestamp, self.primary_tf, htf)
        if bar_count_in_htf >= ratio:
            completed = self.incomplete_bars[symbol][htf]
            self.bars[symbol][htf].append(completed)
            self.incomplete_bars[symbol][htf] = None
            return completed

        return None

    def _bar_starts_new_htf(self, primary_ts: datetime, last_htf_ts: datetime, htf: str) -> bool:
        """Check if primary bar timestamp starts a new HTF bar."""
        # Align primary timestamp to HTF grid
        aligned = self._align_timestamp(primary_ts, htf)
        return aligned > last_htf_ts

    def _align_timestamp(self, ts: datetime, timeframe: str) -> datetime:
        """Align timestamp to timeframe grid."""
        minutes = TIMEFRAME_MINUTES[timeframe]

        if timeframe == "1d":
            # Align to start of day
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)

        # Align to nearest timeframe boundary
        # Floor to nearest multiple of minutes
        total_minutes = ts.hour * 60 + ts.minute
        aligned_minutes = (total_minutes // minutes) * minutes
        aligned_hour = aligned_minutes // 60
        aligned_minute = aligned_minutes % 60

        return ts.replace(hour=aligned_hour, minute=aligned_minute, second=0, microsecond=0)

    def _get_aggregation_ratio(self, from_tf: str, to_tf: str) -> Optional[int]:
        """Get aggregation ratio between timeframes."""
        # Check direct ratio
        if (from_tf, to_tf) in AGGREGATION_RATIOS:
            return AGGREGATION_RATIOS[(from_tf, to_tf)]

        # Check if indirect aggregation is possible
        from_minutes = TIMEFRAME_MINUTES.get(from_tf, 0)
        to_minutes = TIMEFRAME_MINUTES.get(to_tf, 0)

        if from_minutes > 0 and to_minutes > 0 and to_minutes % from_minutes == 0:
            return to_minutes // from_minutes

        return None

    def _count_bars_in_htf(self, symbol: str, htf_ts: datetime, primary_tf: str, htf: str) -> int:
        """Count how many primary bars are in current HTF bar."""
        # This is a simplified count - in production would track more precisely
        ratio = self._get_aggregation_ratio(primary_tf, htf)
        if ratio is None:
            return 0

        # Count primary bars with timestamp >= htf_ts and < htf_ts + period
        minutes = TIMEFRAME_MINUTES[htf]
        htf_end = htf_ts + timedelta(minutes=minutes)

        count = 0
        if symbol in self.bars and primary_tf in self.bars[symbol]:
            for bar in self.bars[symbol][primary_tf]:
                if htf_ts <= bar.timestamp < htf_end:
                    count += 1

        return count

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        count: int = 20,
        include_incomplete: bool = False,
    ) -> List[Bar]:
        """
        Retrieve bars for a symbol and timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe string
            count: Number of bars to retrieve
            include_incomplete: Whether to include incomplete bar

        Returns:
            List of Bar objects (most recent last)
        """
        if symbol not in self.bars or timeframe not in self.bars[symbol]:
            return []

        bars = list(self.bars[symbol][timeframe])

        # Add incomplete bar if requested
        if include_incomplete and symbol in self.incomplete_bars:
            if timeframe in self.incomplete_bars[symbol]:
                incomplete = self.incomplete_bars[symbol][timeframe]
                if incomplete:
                    bars.append(incomplete)

        # Return last N bars
        return bars[-count:] if count > 0 else bars

    def get_last_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get most recent completed bar for timeframe."""
        if symbol not in self.bars or timeframe not in self.bars[symbol]:
            return None

        bars = self.bars[symbol][timeframe]
        return bars[-1] if bars else None

    def get_incomplete_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get incomplete bar for timeframe."""
        if symbol not in self.incomplete_bars or timeframe not in self.incomplete_bars[symbol]:
            return None

        return self.incomplete_bars[symbol][timeframe]

    def to_dataframe(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Convert bars to DataFrame."""
        bars = self.get_bars(symbol, timeframe, count=-1)  # Get all bars

        if not bars:
            return pd.DataFrame()

        data = [bar.to_dict() for bar in bars]
        return pd.DataFrame(data)

    def prune_old_bars(self, symbol: str, keep_count: int = 100) -> None:
        """Prune old bars to save memory."""
        if symbol not in self.bars:
            return

        for timeframe in self.bars[symbol]:
            bars = self.bars[symbol][timeframe]
            while len(bars) > keep_count:
                bars.popleft()

    def clear_symbol(self, symbol: str) -> None:
        """Clear all data for symbol."""
        if symbol in self.bars:
            del self.bars[symbol]
        if symbol in self.incomplete_bars:
            del self.incomplete_bars[symbol]
