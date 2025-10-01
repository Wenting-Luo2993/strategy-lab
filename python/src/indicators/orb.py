import pandas as pd

from src.utils.logger import get_logger
from .ta import IndicatorFactory

logger = get_logger("IndicatorFactory")

@IndicatorFactory.register('orb_levels')
def calculate_orb_levels(
    df: pd.DataFrame,
    start_time: str = "09:30",
    duration_minutes: int = 5,
    time_col: str = None,
    body_pct: float = 0.5
) -> pd.DataFrame:
    """
    Calculate Opening Range Breakout (ORB) levels for a configurable time window.
    
    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns ["Open", "High", "Low", "Close", "Volume"]. Must have a DatetimeIndex or a time column.
    start_time : str
        Opening time in HH:MM format (default "09:30").
    duration_minutes : int
        Length of the opening range in minutes (default 5).
    time_col : str, optional
        If provided, use this column for timestamps instead of index.
    
    Returns
    -------
    pd.DataFrame
        Original df with added columns:
        - "ORB_High"
        - "ORB_Low"
        - "ORB_Range"
        - "ORB_Start"
        - "ORB_End"
    """
    df = df.copy()
    if time_col:
        times = pd.to_datetime(df[time_col])
    else:
        if not isinstance(df.index, pd.DatetimeIndex):
            logger.error("DataFrame index must be a DatetimeIndex or provide time_col")
            raise ValueError("DataFrame index must be a DatetimeIndex or provide time_col")
        times = df.index
    df["date"] = times.date
    df["time"] = times.time
    orb_high = []
    orb_low = []
    orb_start = []
    orb_end = []
    breakout_flags = []

    logger.info(f"Processing {len(df.groupby('date'))} trading days for ORB calculation.")
    for date, group in df.groupby("date"):
        # print(f"Date: {date}, Bars: {len(group)}")
        group_times = pd.to_datetime(group[time_col]) if time_col else group.index
        day_start = pd.Timestamp(f"{date} {start_time}", tz=group_times.tz if hasattr(group_times, 'tz') else None)
        day_end = day_start + pd.Timedelta(minutes=duration_minutes)
        mask = (group_times >= day_start) & (group_times < day_end)
        opening_bars = group[mask]
        if not opening_bars.empty:
            high = opening_bars["high"].max()
            low = opening_bars["low"].min()
            # print(f"ORB window found: High={high}, Low={low}")
        else:
            high = low = None
            # print(f"No ORB window found for {date}")
        orb_high.extend([high] * len(group))
        orb_low.extend([low] * len(group))
        orb_start.extend([day_start] * len(group))
        orb_end.extend([day_end] * len(group))

        flags = get_orb_breakout_flags(group, high, low, body_pct)
        # print(f"Breakout flags for {date}: {flags}")
        breakout_flags.extend(flags)

    df["ORB_High"] = orb_high
    df["ORB_Low"] = orb_low
    df["ORB_Range"] = df["ORB_High"] - df["ORB_Low"]
    # print(f"ORB_Breakout flags length: {len(breakout_flags)}, DataFrame length: {len(df)}")
    df["ORB_Breakout"] = breakout_flags
    if "ORB_Breakout" in df.columns:
        logger.info("ORB_Breakout column successfully added to DataFrame.")
    else:
        logger.error("ORB_Breakout column NOT added to DataFrame!")
    df.drop(["date", "time"], axis=1, inplace=True)
    return df

def get_orb_breakout_flags(group: pd.DataFrame, high: float, low: float, body_pct: float):
    flags = []
    breakout_state = 0  # 0: no breakout, 1: long, -1: short
    for _, row in group.iterrows():
        if high is None or low is None:
            flags.append(0)
            continue
        body = abs(row["close"] - row["open"])
        body_min = min(row["open"], row["close"])
        body_max = max(row["open"], row["close"])
        inside_orb = body_min >= low and body_max <= high
        if inside_orb:
            breakout_state = 0  # Reset breakout state when bar returns inside ORB
            flags.append(0)
        elif row["high"] > high and breakout_state == 0:
            # Long breakout: breakout portion above ORB high
            breakout_size = body_max - high
            if breakout_size > body_pct * body:
                flags.append(1)
                breakout_state = 1
            else:
                flags.append(0)
        elif row["low"] < low and breakout_state == 0:
            # Short breakout: breakout portion below ORB low
            breakout_size = low - body_min
            if breakout_size > body_pct * body:
                flags.append(-1)
                breakout_state = -1
            else:
                flags.append(0)
        else:
            flags.append(0)
    return flags