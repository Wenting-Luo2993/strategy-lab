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
        For long positions: Use ORB low as the stop
        For short positions: Use ORB high as the stop
        
        Args:
            entry_price (float): The price at which the position was entered.
            is_long (bool): True if the position is long, False if short.
            row (pd.Series, optional): The row of the DataFrame with ORB data.
        Returns:
            float: The initial stop loss price based on ORB levels.
        """
        if row is None:
            return None
            
        if is_long:
            # For long positions, use the ORB low as stop
            if "ORB_Low" in row:
                return row["ORB_Low"]
        else:
            # For short positions, use the ORB high as stop
            if "ORB_High" in row:
                return row["ORB_High"]
                
        return None
        
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
        entry_price = None
        take_profit = None
        initial_stop = None
        for i in range(len(df)):
            breakout = df.iloc[i]["ORB_Breakout"]
            close = df.iloc[i]["close"]
            # Entry logic
            if in_position == 0:
                if breakout == 1 or breakout == -1:
                    in_position = breakout
                    entry_price = close
                    is_long = (breakout == 1)
                    take_profit = self.take_profit_value(entry_price, is_long=is_long, row=df.iloc[i])
                    initial_stop = self.initial_stop_value(entry_price, is_long=is_long, row=df.iloc[i])
                    signals.iloc[i] = breakout
            else:
                if self.check_exit(in_position, close, take_profit, i, df, initial_stop=initial_stop):
                    signals.iloc[i] = -in_position
                    in_position = 0
                    entry_price = None
                    take_profit = None
                    initial_stop = None
        return signals