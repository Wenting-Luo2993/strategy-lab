"""
Batch (vectorized) indicator functions for historical research.

These are the batch equivalents of IncrementalIndicatorEngine. They operate on
a full DataFrame at once and return a pd.Series aligned to the input index.

Rules:
- All functions return NaN for the warmup period (first `length` rows).
- No silent gap-filling — NaN propagates naturally through rolling windows.
- ATR uses Wilder's smoothing, identical to _update_atr in engine.py.
"""

import numpy as np
import pandas as pd


def atr_series(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """
    Average True Range using Wilder's smoothing.

    Mirrors IncrementalIndicatorEngine._update_atr: first bar uses (high-low)
    as the TR seed (no prev_close available), then Wilder's smoothing kicks in
    once `length` TRs have accumulated.

    Args:
        df: DataFrame with columns high, low, close.
        length: ATR period.

    Returns:
        pd.Series aligned to df.index; NaN for the first length rows.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    # First bar has no prev_close — use high-low as the seed TR (matches engine.py)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    # Row 0: prev_close is NaN, so TR degenerates to high-low
    tr.iloc[0] = high.iloc[0] - low.iloc[0]

    atr = pd.Series(np.nan, index=df.index, dtype=float)

    # Seed: simple mean of first `length` TRs
    if len(tr) < length:
        return atr

    atr.iloc[length - 1] = tr.iloc[:length].mean()

    # Wilder's smoothing: ATR_t = (ATR_{t-1} * (n-1) + TR_t) / n
    for i in range(length, len(tr)):
        atr.iloc[i] = (atr.iloc[i - 1] * (length - 1) + tr.iloc[i]) / length

    return atr


def sma_series(df: pd.DataFrame, length: int) -> pd.Series:
    """
    Simple moving average of close.

    Args:
        df: DataFrame with column close.
        length: SMA period.

    Returns:
        pd.Series; NaN for rows < length-1.
    """
    return df["close"].rolling(window=length, min_periods=length).mean()


def adx_series(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """
    Average Directional Index (ADX) using Wilder's smoothing.

    Standard Wilder ADX:
      +DM = max(high - prev_high, 0) when > max(prev_low - low, 0), else 0
      -DM = max(prev_low - low, 0)   when > max(high - prev_high, 0), else 0
      TR  = max(high-low, |high-prev_close|, |low-prev_close|)
      Smoothed ATR, +DM14, -DM14 via Wilder's
      +DI14 = 100 * smoothed(+DM14) / smoothed(ATR14)
      -DI14 = 100 * smoothed(-DM14) / smoothed(ATR14)
      DX  = 100 * |+DI14 - -DI14| / (|+DI14| + |-DI14|)
      ADX = Wilder smooth of DX over `length` periods

    Args:
        df: DataFrame with columns high, low, close.
        length: ADX period (typically 14).

    Returns:
        pd.Series; NaN for warmup rows.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]

    plus_dm_s = pd.Series(np.nan, index=df.index, dtype=float)
    minus_dm_s = pd.Series(np.nan, index=df.index, dtype=float)
    tr_s = pd.Series(np.nan, index=df.index, dtype=float)

    n = length
    if len(df) < n + 1:
        return pd.Series(np.nan, index=df.index, dtype=float)

    # Seed on first `length` bars (skip row 0 — no prev values)
    plus_dm_s.iloc[n] = np.sum(plus_dm[1 : n + 1])
    minus_dm_s.iloc[n] = np.sum(minus_dm[1 : n + 1])
    tr_s.iloc[n] = tr.iloc[1 : n + 1].sum()

    for i in range(n + 1, len(df)):
        plus_dm_s.iloc[i] = plus_dm_s.iloc[i - 1] - plus_dm_s.iloc[i - 1] / n + plus_dm[i]
        minus_dm_s.iloc[i] = minus_dm_s.iloc[i - 1] - minus_dm_s.iloc[i - 1] / n + minus_dm[i]
        tr_s.iloc[i] = tr_s.iloc[i - 1] - tr_s.iloc[i - 1] / n + tr.iloc[i]

    plus_di = 100 * plus_dm_s / tr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm_s / tr_s.replace(0, np.nan)

    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)

    adx = pd.Series(np.nan, index=df.index, dtype=float)

    # First valid DX is at index n; seed ADX at index 2n
    first_dx = n
    last_seed = first_dx + n - 1
    if last_seed >= len(df):
        return adx

    adx.iloc[last_seed] = dx.iloc[first_dx : last_seed + 1].mean()

    for i in range(last_seed + 1, len(df)):
        adx.iloc[i] = (adx.iloc[i - 1] * (n - 1) + dx.iloc[i]) / n

    return adx


def linear_slope(series: pd.Series, window: int) -> pd.Series:
    """
    Rolling OLS slope of a price series (as % change per bar).

    Uses numpy polyfit over the rolling window.  Output is the slope of the
    best-fit line divided by the mean price in the window, expressing slope
    as a fraction of price per bar.

    Args:
        series: pd.Series of prices.
        window: Rolling window length.

    Returns:
        pd.Series; NaN for the first window-1 rows.
    """
    result = pd.Series(np.nan, index=series.index, dtype=float)
    x = np.arange(window, dtype=float)

    for i in range(window - 1, len(series)):
        chunk = series.iloc[i - window + 1 : i + 1].values
        if np.isnan(chunk).any():
            continue
        slope = np.polyfit(x, chunk, 1)[0]
        mean_price = chunk.mean()
        result.iloc[i] = slope / mean_price if mean_price != 0 else np.nan

    return result


def rolling_percentile_rank(series: pd.Series, window: int) -> pd.Series:
    """
    Percentile rank of the current value within its rolling window.

    Output is in [0.0, 1.0]:  0.0 = minimum of window, 1.0 = maximum.

    Args:
        series: pd.Series.
        window: Rolling window length.

    Returns:
        pd.Series in [0.0, 1.0]; NaN for the first window-1 rows.
    """
    def _rank(arr: np.ndarray) -> float:
        val = arr[-1]
        below = np.sum(arr[:-1] < val)
        return below / (len(arr) - 1) if len(arr) > 1 else 0.0

    result = series.rolling(window=window, min_periods=window).apply(_rank, raw=True)
    return result.clip(0.0, 1.0)
