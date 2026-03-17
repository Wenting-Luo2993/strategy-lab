"""
Opening Range Breakout (ORB) Strategy Implementation.

Entry: Price breaks ORB_High (long) or ORB_Low (short)
Exit: Take-profit at 2x ATR range, stop-loss at ORB level
Time filter: No entries after 15:00
End-of-day exit for open positions
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import time
import pandas as pd
import numpy as np
from pydantic import Field

from vibe.common.strategies.base import StrategyBase, StrategyConfig
from vibe.common.indicators.orb_levels import ORBCalculator

logger = logging.getLogger(__name__)


class ORBStrategyConfig(StrategyConfig):
    """ORB Strategy configuration."""

    orb_start_time: str = Field(default="09:30", description="ORB window start time (HH:MM)")
    orb_duration_minutes: int = Field(default=5, description="ORB window duration in minutes")
    orb_body_pct_filter: float = Field(
        default=0.5,
        description="Minimum body percentage for valid breakout candle",
    )
    entry_cutoff_time: str = Field(default="15:00", description="No entries after this time")
    take_profit_multiplier: float = Field(default=2.0, description="TP = ORB_Range * multiplier")
    stop_loss_at_level: bool = Field(default=True, description="Stop-loss at ORB level")
    use_volume_filter: bool = Field(default=False, description="Require above-average volume")
    volume_threshold: float = Field(default=1.5, description="Volume must be > average * threshold")
    market_close_time: str = Field(default="16:00", description="Market close time")


class ORBStrategy(StrategyBase):
    """
    Opening Range Breakout strategy for intraday trading.

    Entry conditions:
    - Price breaks above ORB_High (long) or below ORB_Low (short)
    - Within valid time window (before entry_cutoff_time)
    - Optional volume confirmation

    Exit conditions:
    - Take-profit at 2x ORB_Range
    - Stop-loss at ORB level
    - End-of-day exit
    """

    def __init__(self, config: Optional[ORBStrategyConfig] = None):
        """Initialize ORB Strategy."""
        if config is None:
            config = ORBStrategyConfig(name="ORB")
        super().__init__(config)

        self.config: ORBStrategyConfig = config
        # Per-symbol ORB calculators - ORBCalculator caches by date only,
        # so a single shared instance would return the first symbol's levels for all symbols.
        self._orb_calculators: Dict[str, ORBCalculator] = {}
        self._orb_calculator_config = {
            "start_time": config.orb_start_time,
            "duration_minutes": config.orb_duration_minutes,
            "body_pct_filter": config.orb_body_pct_filter,
        }

        # Keep a default calculator for batch generate_signals (operates per-date, no caching issue)
        self.orb_calculator = ORBCalculator(
            start_time=config.orb_start_time,
            duration_minutes=config.orb_duration_minutes,
            body_pct_filter=config.orb_body_pct_filter,
        )

        # Entry cutoff time
        hour, minute = map(int, config.entry_cutoff_time.split(":"))
        self.entry_cutoff = time(hour, minute)

        # Market close time
        hour, minute = map(int, config.market_close_time.split(":"))
        self.market_close = time(hour, minute)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate ORB signals for entire DataFrame.

        Returns Series with signals: 1 (long), -1 (short), 0 (neutral)
        """
        signals = pd.Series(0, index=df.index)

        if df.empty or "ATR_14" not in df.columns:
            return signals

        # Calculate ORB levels for each day
        for date, daily_df in df.groupby(df["timestamp"].dt.date):
            levels = self.orb_calculator.calculate(daily_df)

            if not levels.valid:
                continue

            # Find breakout bars
            for idx, (_, row) in enumerate(daily_df.iterrows()):
                bar_time = row["timestamp"].time()

                # Check time filter
                if bar_time >= self.entry_cutoff:
                    continue

                # Check volume filter if enabled
                if self.config.use_volume_filter:
                    if not self._check_volume_filter(daily_df, idx):
                        continue

                # Long breakout
                if self.orb_calculator.is_long_breakout(row["close"], levels):
                    signals.loc[_] = 1

                # Short breakout
                elif self.orb_calculator.is_short_breakout(row["close"], levels):
                    signals.loc[_] = -1

        return signals

    def _get_orb_calculator(self, symbol: str) -> ORBCalculator:
        """Get or create a per-symbol ORBCalculator instance."""
        if symbol not in self._orb_calculators:
            self._orb_calculators[symbol] = ORBCalculator(**self._orb_calculator_config)
        return self._orb_calculators[symbol]

    def generate_signal_incremental(
        self,
        symbol: str,
        current_bar: Dict[str, float],
        df_context: pd.DataFrame,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Generate signal for current bar incrementally.

        Args:
            symbol: Trading symbol
            current_bar: Current bar {'open', 'high', 'low', 'close', 'volume', 'timestamp'}
            df_context: Historical bars for context

        Returns:
            (signal, metadata)
        """
        # Check if position already open - prevent duplicate entries
        if self.has_position(symbol):
            return 0, {"reason": "position_already_open"}

        if df_context.empty or "ATR_14" not in df_context.columns:
            return 0, {"reason": "insufficient_data"}

        # Get current time
        current_time = current_bar.get("timestamp")
        if current_time is None:
            return 0, {"reason": "no_timestamp"}

        if isinstance(current_time, str):
            from datetime import datetime

            current_time = datetime.fromisoformat(current_time)

        # CRITICAL FIX: Convert to market timezone (EDT/EST) before extracting time
        # This ensures we compare market hours correctly regardless of input timezone
        import pytz
        market_tz = pytz.timezone("America/New_York")

        # Ensure timezone-aware
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=pytz.UTC)

        # Convert to market timezone
        current_time_local = current_time.astimezone(market_tz)
        bar_time = current_time_local.time()

        # DEBUG: Log timestamp comparison details (using INFO level to ensure visibility)
        logger.info(
            f"[TIMESTAMP CHECK] {symbol}: "
            f"current_time={current_time} → market_time={current_time_local}, "
            f"bar_time={bar_time}, "
            f"entry_cutoff={self.entry_cutoff}, "
            f"is_after_cutoff={bar_time >= self.entry_cutoff}"
        )

        # Calculate ORB levels from context, passing current trading date explicitly
        # This ensures we calculate ORB for the current bar's date, not historical data
        # NOTE: Calculate BEFORE checking entry cutoff so ORB levels get stored for notification
        # Use per-symbol calculator to avoid cache collision across symbols
        orb_calc = self._get_orb_calculator(symbol)
        levels = orb_calc.calculate(df_context, trading_date=current_time)

        if not levels.valid:
            return 0, {"reason": "invalid_orb_levels", "reason_detail": levels.reason}

        # Check time filter AFTER calculating ORB (so levels get stored)
        if bar_time >= self.entry_cutoff:
            # Return with ORB levels in metadata (for notification)
            return 0, {
                "reason": "after_entry_cutoff_time",
                "orb_high": levels.high,
                "orb_low": levels.low,
                "orb_range": levels.range,
                "current_price": current_bar["close"],
                "price_position": "n/a",
            }

        # Check volume filter
        if self.config.use_volume_filter:
            avg_volume = df_context["volume"].mean()
            if current_bar["volume"] < avg_volume * self.config.volume_threshold:
                return 0, {"reason": "insufficient_volume"}

        current_price = current_bar["close"]

        # Calculate distance to breakout levels for logging
        distance_to_high = ((levels.high - current_price) / current_price) * 100 if current_price < levels.high else 0
        distance_to_low = ((current_price - levels.low) / current_price) * 100 if current_price > levels.low else 0

        # Determine price position
        if current_price > levels.high:
            price_position = "above_high"
        elif current_price < levels.low:
            price_position = "below_low"
        else:
            price_position = "within_range"

        metadata = {
            "orb_high": levels.high,
            "orb_low": levels.low,
            "orb_range": levels.range,
            "current_price": current_price,
            "current_bar": current_bar,
            "price_position": price_position,
            "distance_to_high_pct": distance_to_high,
            "distance_to_low_pct": distance_to_low,
            "bar_time": bar_time.strftime("%H:%M"),
        }

        # Long breakout
        if orb_calc.is_long_breakout(current_price, levels):
            # Check body percentage filter for breakout bar
            body_pct = self._calculate_body_percentage(current_bar)
            if body_pct < self.config.orb_body_pct_filter:
                # Return full metadata (includes ORB levels) even when rejecting signal
                metadata.update({
                    "reason": "weak_breakout_candle",
                    "reason_detail": f"Body {body_pct:.1%} < {self.config.orb_body_pct_filter:.1%} threshold"
                })
                return 0, metadata

            atr = df_context["ATR_14"].iloc[-1] if "ATR_14" in df_context.columns else levels.range / 2

            tp = orb_calc.get_long_exit_level(
                levels,
                atr,
                multiplier=self.config.take_profit_multiplier,
            )
            sl = levels.low if self.config.stop_loss_at_level else current_price - atr

            metadata.update({
                "signal": "long_breakout",
                "take_profit": tp,
                "stop_loss": sl,
                "risk_reward": (tp - current_price) / (current_price - sl) if current_price > sl else 0,
            })

            return 1, metadata

        # Short breakout
        elif orb_calc.is_short_breakout(current_price, levels):
            # Check body percentage filter for breakout bar
            body_pct = self._calculate_body_percentage(current_bar)
            if body_pct < self.config.orb_body_pct_filter:
                # Return full metadata (includes ORB levels) even when rejecting signal
                metadata.update({
                    "reason": "weak_breakout_candle",
                    "reason_detail": f"Body {body_pct:.1%} < {self.config.orb_body_pct_filter:.1%} threshold"
                })
                return 0, metadata

            atr = df_context["ATR_14"].iloc[-1] if "ATR_14" in df_context.columns else levels.range / 2

            tp = orb_calc.get_short_exit_level(
                levels,
                atr,
                multiplier=self.config.take_profit_multiplier,
            )
            sl = levels.high if self.config.stop_loss_at_level else current_price + atr

            metadata.update({
                "signal": "short_breakout",
                "take_profit": tp,
                "stop_loss": sl,
                "risk_reward": (current_price - tp) / (sl - current_price) if sl > current_price else 0,
            })

            return -1, metadata

        # No breakout - return full metadata (includes ORB levels for notification)
        metadata.update({"reason": "no_breakout"})
        return 0, metadata

    def _calculate_body_percentage(self, bar: dict) -> float:
        """
        Calculate body percentage of a candle.

        Body percentage = |close - open| / (high - low)

        Args:
            bar: Bar dict with open, high, low, close

        Returns:
            Body percentage (0.0 to 1.0)
        """
        open_price = bar.get("open", 0)
        close_price = bar.get("close", 0)
        high_price = bar.get("high", 0)
        low_price = bar.get("low", 0)

        total_range = high_price - low_price
        if total_range == 0:
            return 0.0

        body_size = abs(close_price - open_price)
        return body_size / total_range

    def _check_volume_filter(self, daily_df: pd.DataFrame, current_idx: int) -> bool:
        """Check if current bar has sufficient volume."""
        # Use last 20 bars for average volume
        lookback = min(20, current_idx)
        if lookback == 0:
            return True

        avg_volume = daily_df.iloc[current_idx - lookback : current_idx]["volume"].mean()
        current_volume = daily_df.iloc[current_idx]["volume"]

        return current_volume >= avg_volume * self.config.volume_threshold

    def calculate_exit_level(
        self,
        entry_price: float,
        side: str,
        orb_high: float,
        orb_low: float,
        orb_range: float,
        atr: float,
    ) -> Tuple[float, float]:
        """
        Calculate take-profit and stop-loss levels.

        Args:
            entry_price: Entry price
            side: 'buy' or 'sell'
            orb_high: ORB high level
            orb_low: ORB low level
            orb_range: ORB range (high - low)
            atr: Current ATR

        Returns:
            (take_profit, stop_loss)
        """
        if side == "buy":
            tp = entry_price + (orb_range * self.config.take_profit_multiplier)
            sl = orb_low if self.config.stop_loss_at_level else entry_price - atr
        elif side == "sell":
            tp = entry_price - (orb_range * self.config.take_profit_multiplier)
            sl = orb_high if self.config.stop_loss_at_level else entry_price + atr
        else:
            raise ValueError(f"Invalid side: {side}")

        return tp, sl
