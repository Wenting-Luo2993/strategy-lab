import pandas as pd
from .base import Strategy

class ORBStrategy(Strategy):
    """Opening Range Breakout (ORB) Strategy."""

    def __init__(self, breakout_window=5):
        self.breakout_window = breakout_window  # in minutes (assuming intraday data)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=df.index)
        in_position = 0  # 1 for long, -1 for short, 0 for flat
        entry_price = None
        take_profit = None
        for i in range(len(df)):
            breakout = df.iloc[i]["ORB_Breakout"]
            atr = df.iloc[i]["ATRr_14"]
            close = df.iloc[i]["close"]
            # Entry logic
            if in_position == 0:
                if breakout == 1:
                    in_position = 1
                    entry_price = close
                    take_profit = entry_price + 2 * atr
                    signals.iloc[i] = 1
                elif breakout == -1:
                    in_position = -1
                    entry_price = close
                    take_profit = entry_price - 2 * atr
                    signals.iloc[i] = -1
            else:
                # Exit logic
                if in_position == 1:
                    # Take profit for long
                    if close >= take_profit:
                        signals.iloc[i] = -1
                        in_position = 0
                        entry_price = None
                        take_profit = None
                    # Day end exit
                    elif i == len(df) - 1 or df.index[i].date() != df.index[i+1].date():
                        signals.iloc[i] = -1
                        in_position = 0
                        entry_price = None
                        take_profit = None
                elif in_position == -1:
                    # Take profit for short
                    if close <= take_profit:
                        signals.iloc[i] = 1
                        in_position = 0
                        entry_price = None
                        take_profit = None
                    # Day end exit
                    elif i == len(df) - 1 or df.index[i].date() != df.index[i+1].date():
                        signals.iloc[i] = 1
                        in_position = 0
                        entry_price = None
                        take_profit = None
        return signals