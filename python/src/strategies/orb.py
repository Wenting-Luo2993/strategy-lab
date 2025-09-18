import pandas as pd
from .base import Strategy


class ORBStrategy(Strategy):
    """Opening Range Breakout (ORB) Strategy."""

    def __init__(self, breakout_window=5, strategy_config=None):
        super().__init__(strategy_config=strategy_config)
        self.breakout_window = breakout_window  # in minutes (assuming intraday data)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=df.index)
        in_position = 0  # 1 for long, -1 for short, 0 for flat
        entry_price = None
        take_profit = None
        for i in range(len(df)):
            breakout = df.iloc[i]["ORB_Breakout"]
            close = df.iloc[i]["close"]
            # Entry logic
            if in_position == 0:
                if breakout == 1 or breakout == -1:
                    in_position = breakout
                    entry_price = close
                    take_profit = self.take_profit_value(entry_price, is_long=(breakout == 1), row=df.iloc[i])
                    signals.iloc[i] = breakout
            else:
                if self.check_exit(in_position, close, take_profit, i, df):
                    signals.iloc[i] = -in_position
                    in_position = 0
                    entry_price = None
                    take_profit = None
        return signals