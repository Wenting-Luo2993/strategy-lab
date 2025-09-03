import pandas as pd
import pandas_ta as ta

def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add common technical indicators using pandas_ta.
    """
    # Appends new columns in-place
    df.ta.sma(length=20, append=True)   # adds "SMA_20"
    df.ta.sma(length=50, append=True)   # adds "SMA_50"
    df.ta.rsi(length=14, append=True)   # adds "RSI_14"
    df.ta.atr(length=14, append=True)   # adds "ATRr_14"

    # Print summary for SMA20
    sma20 = df["SMA_20"]
    print(
        f"SMA20 -> non-null count: {sma20.notna().sum()}, "
        f"total rows: {len(sma20)}, "
        f"average: {sma20.mean():.2f}"
    )

    return df
