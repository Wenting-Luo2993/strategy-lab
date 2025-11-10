import pandas as pd

from src.config import StrategyConfig
from src.indicators import IndicatorFactory

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
        # Will be injected by orchestrator (MarketHoursConfig); kept optional for backtests
        self.market_hours = None

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
        # Take profit exit
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
        if self.strategy_config and getattr(self.strategy_config, 'eod_exit', False) and self.market_hours:
            current_ts = df.index[i]
            # If next bar is a different date OR current time has passed the configured close time
            next_is_new_day = (i < len(df) - 1) and (df.index[i].date() != df.index[i+1].date())
            session_closed = current_ts.time() >= getattr(self.market_hours, 'close_time', current_ts.time())
            if next_is_new_day or session_closed:
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
