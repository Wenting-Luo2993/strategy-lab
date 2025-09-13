import pandas as pd
from .base import Strategy

class ORBStrategy(Strategy):
    """Opening Range Breakout (ORB) Strategy."""

    def __init__(self, breakout_window=5):
        self.breakout_window = breakout_window  # in minutes (assuming intraday data)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # TODO: implement ORB logic
        # For now, just return hold
        return pd.Series(0, index=df.index)