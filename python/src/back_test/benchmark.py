"""Benchmark generation utilities.

Provides functions to generate a simple buy & hold benchmark equity/return series
based on the first available price in a DataFrame and optional date range.

Intended usage: reuse existing cached ticker OHLCV DataFrames without creating
additional persistent CSV files; benchmarks are built on the fly for plotting
or comparative analysis.
"""
from __future__ import annotations

from typing import Dict, Optional, Union, List
import pandas as pd

from src.data.cache import CacheDataLoader
from src.data.yahoo import YahooDataLoader

DateLike = Union[str, pd.Timestamp]


def _coerce_timestamp(ts: DateLike) -> pd.Timestamp:
    """Coerce a string or Timestamp into a pandas.Timestamp.
    Raises ValueError if cannot parse.
    """
    if isinstance(ts, pd.Timestamp):
        return ts
    try:
        return pd.Timestamp(ts)
    except Exception as e:
        raise ValueError(f"Could not parse date value '{ts}': {e}") from e


def generate_buy_hold_benchmark(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    price_col: str = "close",
    normalize: bool = True,
    name: Optional[str] = None,
) -> pd.Series:
    """Generate a buy & hold benchmark equity (or normalized return) series.

    Assumes investing the entire initial_capital at the first available price
    in the (optionally) sliced date range and holding to the end.

    Args:
        df: DataFrame containing at least a column specified by price_col and a DateTimeIndex.
        initial_capital: Capital deployed at the start.
        start: Optional inclusive start date (string or Timestamp). If None, use first index.
        end: Optional inclusive end date (string or Timestamp). If None, use last index.
        price_col: Column name for the price (default 'close').
        normalize: If True, returns a series starting at 1.0 (equity / initial_capital). Otherwise raw equity.
        name: Optional name for the returned Series; defaults to 'benchmark_return' if normalize else 'benchmark_equity'.

    Returns:
        pd.Series: Benchmark equity or normalized return series indexed like the sliced DataFrame.
    """
    if price_col not in df.columns:
        raise ValueError(f"Price column '{price_col}' not found in DataFrame.")

    # Apply date filtering if requested
    sliced = df
    if start is not None:
        start_ts = _coerce_timestamp(start)
        sliced = sliced[sliced.index >= start_ts]
    if end is not None:
        end_ts = _coerce_timestamp(end)
        sliced = sliced[sliced.index <= end_ts]

    if sliced.empty:
        raise ValueError("No data available after applying date range filters.")

    first_price = float(sliced[price_col].iloc[0])
    if first_price <= 0:
        raise ValueError("First price must be positive to allocate capital.")

    shares = initial_capital / first_price
    equity_series = sliced[price_col].astype(float) * shares

    if normalize:
        result = equity_series / initial_capital
        result.name = name or "benchmark_return"
    else:
        result = equity_series
        result.name = name or "benchmark_equity"
    return result


def generate_multi_ticker_benchmarks(
    ticker_data: Dict[str, pd.DataFrame],
    initial_capital: float = 10000.0,
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    price_col: str = "close",
    normalize: bool = True,
) -> Dict[str, pd.Series]:
    """Generate buy & hold benchmark series for multiple tickers.

    Args:
        ticker_data: Mapping of ticker -> DataFrame.
        initial_capital: Capital deployed per ticker independently.
        start: Optional inclusive start date filter.
        end: Optional inclusive end date filter.
        price_col: Column name for price in each DataFrame.
        normalize: Return normalized series (start=1.0) when True; else raw equity values.

    Returns:
        dict[str, pd.Series]: Mapping of ticker -> benchmark series.
    """
    benchmarks: Dict[str, pd.Series] = {}
    for ticker, df in ticker_data.items():
        if df is None or df.empty:
            continue
        try:
            benchmarks[ticker] = generate_buy_hold_benchmark(
                df,
                initial_capital=initial_capital,
                start=start,
                end=end,
                price_col=price_col,
                normalize=normalize,
                name=f"{ticker}_benchmark_{'ret' if normalize else 'equity'}"
            )
        except Exception as e:
            # Skip problematic tickers but continue others
            # Could log here if logger available; keep pure utility for now.
            continue
    return benchmarks


def fetch_and_generate_benchmarks(
    tickers: List[str],
    cache_loader = CacheDataLoader(YahooDataLoader()),
    timeframe: str = "5m",
    initial_capital: float = 10000.0,
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    price_col: str = "close",
    normalize: bool = True,
) -> Dict[str, pd.Series]:
    """Convenience wrapper: use an existing CacheDataLoader to fetch each ticker's data
    and produce buy & hold benchmark series.

    Args:
        cache_loader: Instance of CacheDataLoader (implements .fetch(symbol, timeframe, start, end)).
        tickers: List of ticker symbols (strings).
        timeframe: Timeframe string accepted by cache_loader (e.g., '5m').
        initial_capital: Capital deployed per ticker independently.
        start: Optional inclusive start date (YYYY-MM-DD or Timestamp).
        end: Optional inclusive end date.
        price_col: Column in returned DataFrames representing price.
        normalize: If True returns benchmark starting at 1.0; else raw equity values.

    Returns:
        dict[ticker, pd.Series]: Mapping of ticker -> benchmark series.
    """
    ticker_data: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            df = cache_loader.fetch(t, timeframe=timeframe, start=start, end=end)
        except TypeError:
            # Support older signature without keyword names
            df = cache_loader.fetch(t, timeframe, start, end)
        if df is not None and not df.empty:
            ticker_data[t] = df
    return generate_multi_ticker_benchmarks(
        ticker_data,
        initial_capital=initial_capital,
        start=start,
        end=end,
        price_col=price_col,
        normalize=normalize,
    )
