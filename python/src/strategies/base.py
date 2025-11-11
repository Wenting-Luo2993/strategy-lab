import pandas as pd
from datetime import datetime
import pytz

from src.config.orchestrator_config import MarketHoursConfig
from src.config import StrategyConfig
from src.indicators import IndicatorFactory
from src.utils.logger import get_logger

_strategy_logger = get_logger("StrategyBase")

class StrategyBase:
    """Base strategy interface.

    Enhancements:
      * market_hours support (attached externally by orchestrator) for end-of-day exit logic.
      * Optional incremental signal generation via ``generate_signal_incremental`` (implemented in subclasses).
    """

    def __init__(self, strategy_config:StrategyConfig = None, profit_target_func=None):
        """Initialize a strategy base instance.

        Args:
            strategy_config: configuration object for the strategy (see StrategyConfig)
            profit_target_func: legacy hook returning profit target for a row
        """
        self.strategy_config = strategy_config
        self.profit_target_func = profit_target_func
        # default to 4pm market close if not set externally
        self.market_hours: MarketHoursConfig = MarketHoursConfig()

    def initial_stop_value(self, entry_price, is_long, row=None):
        """
        Calculate the initial stop loss value based on entry price, position direction, and row data.
        This method can be overridden by subclasses to provide strategy-specific initial stops.

        Args:
            entry_price (float): The price at which the position was entered.
            is_long (bool): True if the position is long, False if short.
            row (pd.Series, optional): The row of the DataFrame (e.g., df.iloc[i]) for additional context.
        Returns:
            float or None: The initial stop loss price. Return None to defer to risk management.
        """
        # Base implementation defers to risk management by returning None
        return None

    def take_profit_value(self, entry_price, is_long, row=None):
        """
        Calculate the take profit value based on entry price, position direction, and row data.
        Args:
            entry_price (float): The price at which the position was entered.
            is_long (bool): True if the position is long, False if short.
            row (pd.Series, optional): The row of the DataFrame (e.g., df.iloc[i]) for additional context.
        Returns:
            float: The take profit price.
        """
        if self.strategy_config is None or row is None:
            raise ValueError("strategy_config and row are required for take_profit_value calculation.")

        tp_type = self.strategy_config.risk.take_profit_type
        tp_value = self.strategy_config.risk.take_profit_value

        if tp_type == "atr":
            atr = row.get("ATRr_14")
            if atr is None:
                # ATR column doesn't exist, use indicator factory to add it
                df_copy = IndicatorFactory.apply(row.to_frame().T, [
                    {'name': 'atr', 'params': {'length': 14}}
                ])
                atr = df_copy.iloc[0].get("ATRr_14")
                if atr is None:
                    # Still can't calculate ATR (probably not enough data)
                    # Use a default percentage instead
                    atr = entry_price * 0.02  # Default to 2% of entry price
            if is_long:
                return entry_price + tp_value * atr
            else:
                return entry_price - tp_value * atr
        elif tp_type == "percent":
            if is_long:
                return entry_price * (1 + tp_value / 100)
            else:
                return entry_price * (1 - tp_value / 100)
        else:
            raise NotImplementedError(f"Unknown take profit type: {tp_type}")

    def check_exit(self, position_type, close, take_profit, i, df, initial_stop=None):
        """
        Determines if an exit condition is met for the current position.
        Args:
            position_type (int): 1 for long, -1 for short.
            close (float): Current close price.
            take_profit (float): Take profit price.
            i (int): Current index in the DataFrame.
            df (pd.DataFrame): The full DataFrame (for EOD logic).
            initial_stop (float, optional): Initial stop loss price.
        Returns:
            bool: True if exit condition is met, False otherwise.
        """
        # Take profit exit (guard None)
        if take_profit is not None:
            if position_type == 1 and close >= take_profit:
                return True
            if position_type == -1 and close <= take_profit:
                return True
        # Initial stop loss exit (if provided)
        if initial_stop is not None:
            if position_type == 1 and close <= initial_stop:
                return True
            if position_type == -1 and close >= initial_stop:
                return True
        # End-of-day session exit based on configured market hours (if enabled in strategy config)
        # We avoid using the synthetic "last row" heuristic which caused premature exits.
        if self.strategy_config and getattr(self.strategy_config, 'eod_exit', False) and self.market_hours and position_type != 0:
            current_ts = df.index[i]
            close_time = getattr(self.market_hours, 'close_time', None)
            if close_time is None:
                from datetime import time as _t
                close_time = _t(16, 0)

            # Normalize current timestamp to New York timezone for session boundary math
            ny_tz = pytz.timezone('America/New_York')
            if current_ts.tzinfo is not None:
                current_ny = current_ts.astimezone(ny_tz)
            else:
                # Assume data already in session timezone if naive
                current_ny = ny_tz.localize(datetime.combine(current_ts.date(), current_ts.time()))

            session_end_ny = ny_tz.localize(datetime.combine(current_ny.date(), close_time))
            minutes_to_close = (session_end_ny - current_ny).total_seconds() / 60.0
            if minutes_to_close < 0:
                minutes_to_close = 0  # already past close

            next_is_new_day = (i < len(df) - 1) and (df.index[i].date() != df.index[i+1].date())
            session_closed = current_ny.time() >= close_time
            within_pre_close_window = 0 <= minutes_to_close <= 11
            last_bar_of_day = (i == len(df) - 1)
            # Infer bar interval
            bar_interval_min = None
            if len(df) >= 2:
                try:
                    delta = (df.index[i] - df.index[i-1]).total_seconds() / 60.0
                    if 0 < delta <= 120:
                        bar_interval_min = delta
                except Exception:
                    bar_interval_min = None
            near_close_fallback = last_bar_of_day and bar_interval_min is not None and 0 <= minutes_to_close <= bar_interval_min

            _strategy_logger.debug(
                "eod.check",
                extra={"meta": {
                    "ts": str(current_ts),
                    "position_type": position_type,
                    "minutes_to_close": minutes_to_close,
                    "session_closed": session_closed,
                    "within_pre_close_window": within_pre_close_window,
                    "next_is_new_day": next_is_new_day,
                    "last_bar_of_day": last_bar_of_day,
                    "near_close_fallback": near_close_fallback
                }}
            )
            if next_is_new_day or session_closed or within_pre_close_window or near_close_fallback:
                _strategy_logger.info("eod.exit.trigger", extra={"meta": {"ts": str(current_ts), "minutes_to_close": minutes_to_close}})
                return True
        return False

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate trading signals for given OHLCV data.

        Args:
            df (pd.DataFrame): DataFrame with OHLCV + indicators.

        Returns:
            pd.Series: A pandas Series with the same index as df, containing:
                       +1 = Buy/Long entry
                       -1 = Sell/Short entry or exit from long position
                       0 = Hold/No action

        Note:
            The returned Series must have the same index as the input DataFrame
            and must contain only integer values (1, -1, 0).
        """
        raise NotImplementedError

    def generate_signal_incremental(self, df: pd.DataFrame) -> tuple[int, bool]:
        """
        Generate incremental trading signals based on the latest data point.

        Args:
            df (pd.DataFrame): DataFrame with OHLCV + indicators.

        Returns:
            tuple[int, bool]: A tuple containing:
                              - An integer signal (+1 for long entry, -1 for short entry/exit, 0 for hold)
                              - A boolean exit_flag indicating if an explicit exit is requested
        """
        raise NotImplementedError("Incremental signal generation not implemented for this strategy.")
