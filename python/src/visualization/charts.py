import mplfinance as mpf
import pandas as pd
from typing import Optional, List, Union
from datetime import datetime

from src.data.cache import CacheDataLoader  # cache-only mode
from src.indicators import calculate_orb_levels

def plot_candlestick(df, indicators=None, moreplots=None, title="Candlestick Chart"):
    """Plots a candlestick chart with optional indicator overlays.

    Args:
        df (pd.DataFrame): DataFrame with OHLCV columns in lowercase.
        indicators (list[str] | None): Indicator column names to overlay.
        moreplots: Additional mplfinance addplot objects.
        title (str): Chart title.
    """
    if indicators is None:
        indicators = []

    addplots = moreplots if moreplots else []
    for ind in indicators:
        if ind in df.columns:
            addplots.append(mpf.make_addplot(df[ind], panel=0, ylabel=ind))

    return mpf.plot(
        df,
        type="candle",
        style="yahoo",
        addplot=addplots,
        title=title,
        ylabel="Price",
        volume=True,
        figratio=(12, 6),
        figscale=1.2,
    )

def plot_cache_time_range(
    source: Union[pd.DataFrame, str],
    timeframe: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    indicators: Optional[List[str]] = None,
    title: Optional[str] = None,
    max_rows: Optional[int] = None,
    cache_dir: str = "data_cache",
    compute_missing_orb: bool = True,
):
    """Plot a specific time range of cached data.

    Two usage modes:
    1) Backward compatible (old signature): pass a DataFrame as first arg and omit timeframe.
       Example: plot_cache_time_range(df, start=..., end=...)
    2) New fetch mode: pass a symbol string as first arg and provide timeframe (e.g. '5m').
       The function will internally create a DataLoader + CacheDataLoader and fetch the data.

    Args:
        source: DataFrame OR ticker symbol (str).
        timeframe: Required when source is a symbol (e.g. '1m','5m','15m','1d'). Ignored if source is DataFrame.
        start: Inclusive start as a datetime (timezone-aware recommended). Defaults to earliest available.
        end: Inclusive end as a datetime (timezone-aware recommended). Defaults to latest available.
        indicators: Indicator columns to overlay if present.
        title: Custom chart title; auto-generated if None.
        max_rows: Downsample for performance if row count exceeds this number.
        cache_dir: Cache directory path.
        compute_missing_orb: If True and indicators request ORB columns not present, try to compute them.
    """
    if indicators is None:
        indicators = []

    def _normalize_to_index_tz(ts, idx_tz):
        if ts is None:
            return None
        if not isinstance(ts, pd.Timestamp):
            ts = pd.Timestamp(ts)
        # If index has timezone, align. If ts is naive, localize. If different tz, convert.
        if idx_tz is not None:
            if ts.tzinfo is None:
                ts = ts.tz_localize(idx_tz)
            elif ts.tzinfo != idx_tz:
                ts = ts.tz_convert(idx_tz)
        else:
            # Ensure naive if index naive
            if ts.tzinfo is not None:
                ts = ts.tz_convert('UTC').tz_localize(None) if idx_tz is None else ts
        return ts

    # Determine if we are in DataFrame mode
    if isinstance(source, pd.DataFrame):
        df = source.copy()
    else:
        symbol = source
        if timeframe is None:
            raise ValueError("timeframe must be provided when source is a symbol.")
        # Cache-only loader (no external fetch). If required rows missing they won't be fetched.
        cached = CacheDataLoader(wrapped_loader=None, cache_dir=cache_dir)
        fetch_start = start.date() if start is not None else None
        fetch_end = end.date() if end is not None else None
        df = cached.fetch(symbol, timeframe=timeframe, start=fetch_start, end=fetch_end)
        if df is None or df.empty:
            raise ValueError(f"No cached data for {symbol} {timeframe} in {cache_dir}.")
        # If intraday time bounds provided refine slice
        if start is not None or end is not None:
            idx_tz = getattr(df.index, 'tz', None)
            start_ts = _normalize_to_index_tz(start, idx_tz) if start else df.index.min()
            end_ts = _normalize_to_index_tz(end, idx_tz) if end else df.index.max()
            df = df[(df.index >= start_ts) & (df.index <= end_ts)].copy()

    # Compute missing ORB levels if requested and possible
    if compute_missing_orb and any(ind in ["ORB_High", "ORB_Low"] for ind in indicators):
        if calculate_orb_levels and ("ORB_High" not in df.columns or "ORB_Low" not in df.columns):
            try:
                df = calculate_orb_levels(df)
            except Exception:  # pragma: no cover
                pass

    if df.empty:
        raise ValueError("Provided/sliced DataFrame is empty; nothing to plot.")

    # Resolve start/end if still None (DataFrame mode or after symbol fetch)
    idx_tz = getattr(df.index, 'tz', None)
    start_final = start if start is not None else df.index.min()
    end_final = end if end is not None else df.index.max()
    start_final = _normalize_to_index_tz(start_final, idx_tz)
    end_final = _normalize_to_index_tz(end_final, idx_tz)
    sliced = df[(df.index >= start_final) & (df.index <= end_final)].copy()
    if sliced.empty:
        raise ValueError("Selected time range produced no rows.")
    if max_rows and len(sliced) > max_rows:
        stride = max(len(sliced) // max_rows, 1)
        sliced = sliced.iloc[::stride]
    if title is None:
        date_range_str = f"{sliced.index.min().date()}"
        if sliced.index.min().date() != sliced.index.max().date():
            date_range_str += f" - {sliced.index.max().date()}"
        title = f"Cached Data Range: {date_range_str}"
    return plot_candlestick(sliced, indicators=indicators, title=title)
