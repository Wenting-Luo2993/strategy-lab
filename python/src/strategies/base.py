import pandas as pd

from src.backtester.parameters import StrategyConfig

class Strategy:
    """Base strategy interface."""

    def __init__(self, strategy_config:StrategyConfig = None, profit_target_func=None):
        """
        strategy_config: configuration object for the strategy (see StrategyConfig)
        profit_target_func: function(df, i) -> float, returns the profit target value for the given row (optional, legacy)
        """
        self.strategy_config = strategy_config
        self.profit_target_func = profit_target_func

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
                raise ValueError("ATRr_14 is required in row for ATR-based take profit.")
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

    def check_exit(self, position_type, close, take_profit, i, df):
        """
        Determines if an exit condition is met for the current position.
        Args:
            position_type (int): 1 for long, -1 for short.
            close (float): Current close price.
            take_profit (float): Take profit price.
            i (int): Current index in the DataFrame.
            df (pd.DataFrame): The full DataFrame (for EOD logic).
        Returns:
            bool: True if exit condition is met, False otherwise.
        """
        # Take profit exit
        if position_type == 1 and close >= take_profit:
            return True
        if position_type == -1 and close <= take_profit:
            return True
        # End of day exit
        if i == len(df) - 1:
            return True
        if df.index[i].date() != df.index[i+1].date():
            return True
        return False

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate trading signals for given OHLCV data.
        
        Args:
            df (pd.DataFrame): DataFrame with OHLCV + indicators.
        
        Returns:
            pd.Series: +1 = Buy, -1 = Sell, 0 = Hold
        """
        raise NotImplementedError