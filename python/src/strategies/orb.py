import pandas as pd
from .base import StrategyBase
from ..indicators import calculate_orb_levels, IndicatorFactory
from src.utils.logger import get_logger


class ORBStrategy(StrategyBase):
    """Opening Range Breakout (ORB) Strategy."""

    def __init__(self, breakout_window=5, strategy_config=None, logger=None):
        # Provide a strategy-specific logger name so base + child logs coalesce
        if logger is None:
            logger = get_logger("ORBStrategy")
        super().__init__(strategy_config=strategy_config, logger=logger)
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
                    self.logger.info(
                        f"[{str(df.index[i])}] entry.flag",
                        extra={"meta": {
                            "ts": str(df.index[i]),
                            "signal": breakout,
                            "close": close,
                            "entry_price": entry_price,
                            "take_profit": take_profit,
                            "initial_stop": initial_stop,
                            "orb_low": df.iloc[i].get("ORB_Low"),
                            "orb_high": df.iloc[i].get("ORB_High"),
                            "reason": "orb_breakout.long" if breakout == 1 else "orb_breakout.short"
                        }}
                    )
            else:
                should_exit, reason = self.check_exit(in_position, close, take_profit, i, df, initial_stop=initial_stop)
                if should_exit:
                    signals.iloc[i] = -in_position
                    in_position = 0
                    entry_price = None
                    take_profit = None
                    initial_stop = None
                    # Do NOT clear entry_day to enforce single entry per day
                    self.logger.info(
                        f"[{str(df.index[i])}] exit.signal",
                        extra={"meta": {
                            "ts": str(df.index[i]),
                            "signal": signals.iloc[i],
                            "reason": reason,
                            "close": close
                        }}
                    )
        return signals


    def get_last_exit_reason(self, ticker: str) -> str | None:
        if hasattr(self, '_last_exit_reasons'):
            return self._last_exit_reasons.get(ticker)
        return getattr(self, '_last_exit_reason', None)

    # --- New Stateless Context-Based API ----------------------------------------------------
    def generate_signal_incremental_ctx(self, df: pd.DataFrame, position_ctx: dict | None) -> tuple[int, bool, dict | None]:
        """Stateless incremental signal evaluation using external position context.

        Args:
            df: DataFrame containing up-to-date bars (indicators may be appended here).
            position_ctx: None if flat; otherwise dict with keys:
                in_position (int: -1/0/1), entry_price (float), take_profit (float|None),
                initial_stop (float|None), entry_day (date)

        Returns:
            (entry_signal, exit_flag, new_ctx_or_existing_or_none)
            entry_signal: 1 (long), -1 (short), 0 (no new entry)
            exit_flag: True if an exit should occur now for this context
            context: New context on entry, same context if holding, or None if flat/exit
        """
        if df.empty:
            return 0, False, position_ctx

        # Ensure ORB indicators present for latest bar
        if "ORB_Breakout" not in df.columns or ("ORB_Low" not in df.columns or "ORB_High" not in df.columns):
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
        current_day = row.name.date()

        # Re-entry configuration: simple boolean toggle
        allow_reentry = False
        try:
            if self.strategy_config and getattr(self.strategy_config, 'orb_config', None):
                # Prefer new boolean; fall back to legacy max_entries_per_day if present (>1 implies allow)
                if hasattr(self.strategy_config.orb_config, 'allow_same_day_reentry'):
                    allow_reentry = bool(getattr(self.strategy_config.orb_config, 'allow_same_day_reentry'))
                elif hasattr(self.strategy_config.orb_config, 'max_entries_per_day'):
                    allow_reentry = getattr(self.strategy_config.orb_config, 'max_entries_per_day') > 1
        except Exception:
            allow_reentry = False

        # Flat or context indicates flat: evaluate potential entry while enforcing per-day limit
        if position_ctx is None or position_ctx.get('in_position', 0) == 0:
            prior_entry_day = position_ctx.get('entry_day') if position_ctx else None
            # Block re-entry if same day and flag disabled
            if prior_entry_day == current_day and not allow_reentry:
                return 0, False, position_ctx
            if breakout in (1, -1):
                is_long = breakout == 1
                take_profit = self.take_profit_value(close, is_long=is_long, row=row)
                if take_profit is None:
                    raise ValueError("take_profit should never be None for incremental entry; check indicators/config.")
                initial_stop = self.initial_stop_value(close, is_long=is_long, row=row)
                if initial_stop is None:
                    raise ValueError("initial_stop should never be None for incremental entry; OR levels missing.")
                new_ctx = {
                    'in_position': breakout,
                    'entry_price': close,
                    'take_profit': take_profit,
                    'initial_stop': initial_stop,
                    'entry_day': current_day,
                    # For compatibility keep entries_today if upstream still references it
                    'entries_today': 1
                }
                self._last_exit_reason = None
                self.logger.info(
                    f"[{str(row.name)}] entry.flag.incremental.ctx",
                    extra={"meta": {
                        "signal": breakout,
                        "close": close,
                        "entry_price": close,
                        "take_profit": take_profit,
                        "initial_stop": initial_stop,
                        "orb_low": row.get("ORB_Low"),
                        "orb_high": row.get("ORB_High"),
                        "allow_same_day_reentry": allow_reentry,
                        "reason": "orb_breakout.long" if breakout == 1 else "orb_breakout.short"
                    }}
                )
                return breakout, False, new_ctx
            # Remain flat; preserve prior context (could contain prior_entry_day for blocking)
            return 0, False, position_ctx

        # Holding a position: evaluate exit
        in_pos = position_ctx.get('in_position', 0)
        if in_pos != 0:
            should_exit, reason = self.check_exit(
                in_pos,
                close,
                position_ctx.get('take_profit'),
                last_idx,
                df,
                initial_stop=position_ctx.get('initial_stop')
            )
            if should_exit:
                self._last_exit_reason = reason
                self.logger.info(
                    f"[{str(row.name)}] exit.flag.incremental.ctx",
                    extra={"meta": {
                        "signal": -in_pos,
                        "reason": reason,
                        "close": close
                    }}
                )
                # Preserve entry_day to block further entries this day
                hold_ctx = {
                    'in_position': 0,
                    'entry_price': None,
                    'take_profit': None,
                    'initial_stop': None,
                    # Preserve entry_day only if we disallow re-entry; else clear it so fresh entries allowed
                    'entry_day': position_ctx.get('entry_day') if not allow_reentry else None,
                    'entries_today': 1
                }
                return 0, True, hold_ctx
            # Still holding; enforce single entry per day via entry_day logic externally
            return 0, False, position_ctx

        # Position context claims flat but we treat it as None now
        return 0, False, None
