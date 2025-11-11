import pandas as pd
from .base import StrategyBase
from ..indicators import calculate_orb_levels, IndicatorFactory


class ORBStrategy(StrategyBase):
    """Opening Range Breakout (ORB) Strategy."""

    def __init__(self, breakout_window=5, strategy_config=None):
        super().__init__(strategy_config=strategy_config)
        self.breakout_window = breakout_window  # in minutes (assuming intraday data)

    def initial_stop_value(self, entry_price, is_long, row=None):
        """
        Calculate the initial stop loss value based on ORB range.

        Configurable behavior:
    If strategy_config.orb_config.initial_stop_orb_pct is provided (pct within 0-1),
        compute a blended stop relative to the Opening Range (OR):
            OR = OR_High - OR_Low
            Long  stop = OR_Low + pct * OR
            Short stop = OR_High - pct * OR
        Else fallback to classic behavior (OR_Low for long, OR_High for short).

        Args:
            entry_price (float): The price at which the position was entered.
            is_long (bool): True if the position is long, False if short.
            row (pd.Series, optional): The row of the DataFrame with ORB data.
        Returns:
            float: The initial stop loss price based on ORB levels.
        """
        if row is None:
            return None
        orb_low = row.get("ORB_Low")
        orb_high = row.get("ORB_High")
        if orb_low is None or orb_high is None:
            return None
        rng = orb_high - orb_low
        pct = None
        if self.strategy_config and self.strategy_config.orb_config:
            pct = getattr(self.strategy_config.orb_config, "initial_stop_orb_pct", None)
        if pct is not None and 0 <= pct <= 1 and rng is not None:
            if is_long:
                return orb_low + pct * rng
            else:
                return orb_high - pct * rng
        # Fallback legacy behavior
        return orb_low if is_long else orb_high

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # Check if ORB_Breakout column exists, if not calculate it
        if "ORB_Breakout" not in df.columns:
            # Use indicator factory with config parameters
            start_time = self.strategy_config.orb_config.start_time if self.strategy_config else "09:30"
            duration_minutes = int(self.strategy_config.orb_config.timeframe) if self.strategy_config else 5
            body_pct = self.strategy_config.orb_config.body_breakout_percentage if self.strategy_config else 0.5

            df = IndicatorFactory.apply(df, [
                {'name': 'orb_levels', 'params': {
                    'start_time': start_time,
                    'duration_minutes': duration_minutes,
                    'body_pct': body_pct
                }}
            ])

        signals = pd.Series(0, index=df.index)
        in_position = 0  # 1 for long, -1 for short, 0 for flat
        entry_day = None  # Track the calendar date of first entry; block re-entry same day
        entry_price = None
        take_profit = None
        initial_stop = None
        for i in range(len(df)):
            breakout = df.iloc[i]["ORB_Breakout"]
            close = df.iloc[i]["close"]
            current_day = df.index[i].date()
            # Reset entry_day when new trading day starts and we're flat
            if entry_day is not None and current_day != entry_day and in_position == 0:
                entry_day = None
            if in_position == 0:
                if entry_day is None and breakout in (1, -1):
                    in_position = breakout
                    entry_price = close
                    is_long = breakout == 1
                    take_profit = self.take_profit_value(entry_price, is_long=is_long, row=df.iloc[i])
                    if take_profit is None:
                        raise ValueError("take_profit should never be None after entry; check configuration or indicators.")
                    initial_stop = self.initial_stop_value(entry_price, is_long=is_long, row=df.iloc[i])
                    if initial_stop is None:
                        raise ValueError("initial_stop should never be None after entry; ensure OR levels are present.")
                    signals.iloc[i] = breakout
                    entry_day = current_day
            else:
                if self.check_exit(in_position, close, take_profit, i, df, initial_stop=initial_stop):
                    signals.iloc[i] = -in_position
                    in_position = 0
                    entry_price = None
                    take_profit = None
                    initial_stop = None
                    # Do NOT clear entry_day to enforce single entry per day
        return signals

    # Incremental generation: returns (entry_signal, exit_signal) for the latest bar only.
    # entry_signal: 1 (long entry), -1 (short entry), 0 (no new entry)
    # exit_signal: True if an exit should occur on this bar, else False
    def generate_signal_incremental(self, df: pd.DataFrame) -> tuple[int, bool]:
        if df.empty:
            return 0, False
        # Ensure indicators present for last bar evaluation
        if "ORB_Breakout" not in df.columns:
            start_time = self.strategy_config.orb_config.start_time if self.strategy_config else "09:30"
            duration_minutes = int(self.strategy_config.orb_config.timeframe) if self.strategy_config else 5
            body_pct = self.strategy_config.orb_config.body_breakout_percentage if self.strategy_config else 0.5
            df = IndicatorFactory.apply(df, [
                {'name': 'orb_levels', 'params': {
                    'start_time': start_time,
                    'duration_minutes': duration_minutes,
                    'body_pct': body_pct
                }}
            ])
        else:
            # Ensure ORB_Low/High present when Breakout column already exists (subset slice edge case)
            if "ORB_Low" not in df.columns or "ORB_High" not in df.columns:
                start_time = self.strategy_config.orb_config.start_time if self.strategy_config else "09:30"
                duration_minutes = int(self.strategy_config.orb_config.timeframe) if self.strategy_config else 5
                body_pct = self.strategy_config.orb_config.body_breakout_percentage if self.strategy_config else 0.5
                df = IndicatorFactory.apply(df, [
                    {'name': 'orb_levels', 'params': {
                        'start_time': start_time,
                        'duration_minutes': duration_minutes,
                        'body_pct': body_pct
                    }}
                ])
        last_idx = len(df) - 1
        row = df.iloc[last_idx]
        breakout = row.get("ORB_Breakout", 0)
        close = row.get("close")
        # Track persistent position state on the strategy instance
        if not hasattr(self, '_in_position'):
            self._in_position = 0
            self._entry_price = None
            self._take_profit = None
            self._initial_stop = None
        # Track single-entry-per-day state
        if not hasattr(self, '_entry_day'):
            self._entry_day = None
        entry_signal = 0
        exit_flag = False
        current_day = df.index[last_idx].date()
        # Allow a new entry only if day advanced since last entry (and we are flat)
        if self._entry_day is not None and current_day != self._entry_day and self._in_position == 0:
            self._entry_day = None
        if self._in_position == 0 and breakout in (1, -1):
            # Enforce single entry per trading day
            if self._entry_day is None:
                self._in_position = breakout
                self._entry_price = close
                is_long = breakout == 1
                self._take_profit = self.take_profit_value(self._entry_price, is_long=is_long, row=row)
                if self._take_profit is None:
                    raise ValueError("take_profit should never be None for incremental entry; check indicators/config.")
                self._initial_stop = self.initial_stop_value(self._entry_price, is_long=is_long, row=row)
                if self._initial_stop is None:
                    raise ValueError("initial_stop should never be None for incremental entry; OR levels missing.")
                entry_signal = breakout
                self._entry_day = current_day
        elif self._in_position != 0:
            # Evaluate exit only on latest bar
            if self.check_exit(self._in_position, close, self._take_profit, last_idx, df, initial_stop=self._initial_stop):
                exit_flag = True
                # Reset state
                self._in_position = 0
                self._entry_price = None
                self._take_profit = None
                self._initial_stop = None
                # Do NOT clear _entry_day to block further entries same day
        return entry_signal, exit_flag
