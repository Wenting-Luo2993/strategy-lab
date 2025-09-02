import pandas as pd
import pandas_ta as ta

def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add common technical indicators using pandas_ta.
    """
    df["sma20"] = ta.sma(df["close"], length=20)
    df["sma30"] = ta.sma(df["close"], length=30)
    df["sma50"] = ta.sma(df["close"], length=50)
    df["rsi14"] = ta.rsi(df["close"], length=14)
    df["atr14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    return df
