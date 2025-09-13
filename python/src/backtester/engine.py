import pandas as pd

class Backtester:
    def __init__(self, initial_capital=10000):
        self.initial_capital = initial_capital

    def run(self, df: pd.DataFrame, signals: pd.Series) -> pd.DataFrame:
        """
        Runs a basic backtest (long-only for now).
        
        Args:
            df (pd.DataFrame): OHLCV data with 'close'
            signals (pd.Series): trading signals (+1 = buy, -1 = sell, 0 = hold)

        Returns:
            pd.DataFrame with added 'equity' column
        """
        cash = self.initial_capital
        position = 0
        equity_curve = []

        for i in range(len(df)):
            signal = signals.iloc[i]
            price = df["close"].iloc[i]

            # Buy
            if signal == 1 and cash > 0:
                position = cash / price
                cash = 0

            # Sell
            elif signal == -1 and position > 0:
                cash = position * price
                position = 0

            # Track equity
            equity = cash + position * price
            equity_curve.append(equity)

        df = df.copy()
        df["equity"] = equity_curve
        return df
