import pandas as pd

def calculate_orb_levels(df: pd.DataFrame, bars: int = 1) -> pd.DataFrame:
    """
    Calculate Opening Range Breakout (ORB) levels.
    
    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns ["Open", "High", "Low", "Close", "Volume"].
        Must include a datetime index.
    bars : int
        Number of initial bars to define the opening range (default = 1 bar).
        Example: bars=1 → 1st 5-min bar, bars=6 → 30 min if using 5-min data.
    
    Returns
    -------
    pd.DataFrame
        Original df with added columns:
        - "ORB_High"
        - "ORB_Low"
        - "ORB_Range"
    """
    df = df.copy()

    # Ensure index is datetime
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame index must be a DatetimeIndex")

    # Group by date (each trading day)
    grouped = df.groupby(df.index.date)

    orb_high = []
    orb_low = []

    for _, group in grouped:
        # Get opening range (first N bars of the day)
        opening_bars = group.iloc[:bars]
        high = opening_bars["high"].max()
        low = opening_bars["low"].min()

        # Fill same value for that day
        orb_high.extend([high] * len(group))
        orb_low.extend([low] * len(group))

    df["ORB_High"] = orb_high
    df["ORB_Low"] = orb_low
    df["ORB_Range"] = df["ORB_High"] - df["ORB_Low"]

    return df