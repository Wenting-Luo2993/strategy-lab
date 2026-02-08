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
        if df_context.empty or "ATR_14" not in df_context.columns:
            return 0, {"reason": "insufficient_data"}

        # Get current time
        current_time = current_bar.get("timestamp")
        if current_time is None:
            return 0, {"reason": "no_timestamp"}

        if isinstance(current_time, str):
            from datetime import datetime

            current_time = datetime.fromisoformat(current_time)

        bar_time = current_time.time()

        # Check time filter
        if bar_time >= self.entry_cutoff:
            return 0, {"reason": "after_entry_cutoff_time"}

        # Calculate ORB levels from context
        levels = self.orb_calculator.calculate(df_context)

        if not levels.valid:
            return 0, {"reason": "invalid_orb_levels", "reason_detail": levels.reason}

        # Check volume filter
        if self.config.use_volume_filter:
            avg_volume = df_context["volume"].mean()
            if current_bar["volume"] < avg_volume * self.config.volume_threshold:
                return 0, {"reason": "insufficient_volume"}

        current_price = current_bar["close"]
        metadata = {
            "orb_high": levels.high,
            "orb_low": levels.low,
            "orb_range": levels.range,
        }

        # Long breakout
        if self.orb_calculator.is_long_breakout(current_price, levels):
            atr = df_context["ATR_14"].iloc[-1] if "ATR_14" in df_context.columns else levels.range / 2

            tp = self.orb_calculator.get_long_exit_level(
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
        elif self.orb_calculator.is_short_breakout(current_price, levels):
            atr = df_context["ATR_14"].iloc[-1] if "ATR_14" in df_context.columns else levels.range / 2

            tp = self.orb_calculator.get_short_exit_level(
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

        return 0, {"reason": "no_breakout"}

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
