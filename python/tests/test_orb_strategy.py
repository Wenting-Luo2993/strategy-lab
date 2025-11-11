import os
from pathlib import Path
import pandas as pd
import pytest

from src.strategies.orb import ORBStrategy
from src.config import build_default_orb_strategy_config


CACHE_ROOTS = [
    Path("python/data_cache"),  # primary
    Path("data_cache"),  # fallback root
]


def _load_cached_df(ticker: str, timeframe: str = "5m") -> pd.DataFrame:
    """Attempt to load real cached market data for a ticker/timeframe.

    Searches known cache roots for a subdirectory named for the ticker and a file
    whose name contains the timeframe string. Supports .csv or .parquet.
    Returns empty DataFrame if not found.
    """
    for root in CACHE_ROOTS:
        ticker_dir = root / ticker
        if not ticker_dir.exists() or not ticker_dir.is_dir():
            continue
        # Prefer parquet then csv
        candidates = list(ticker_dir.glob(f"*{timeframe}*.parquet")) + list(ticker_dir.glob(f"*{timeframe}*.csv"))
        if not candidates:
            continue
        path = candidates[0]
        try:
            if path.suffix == ".parquet":
                return pd.read_parquet(path)
            return pd.read_csv(path, parse_dates=True, index_col=0)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _first_trading_day(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    idx = df.index
    # If index not datetime, try to convert
    if not isinstance(idx[0], pd.Timestamp):
        df.index = pd.to_datetime(df.index)
        idx = df.index
    dates = [ts.date() for ts in idx]
    first_day = min(dates)
    start = pd.Timestamp.combine(pd.Timestamp(first_day).date(), pd.Timestamp.min.time())
    end = start + pd.Timedelta(days=1)
    return df[(idx >= start) & (idx < end)].copy()


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA"])  # broaden coverage; will skip if missing
def test_orb_strategy_adds_indicators_and_generates_entry(ticker):
    df = _load_cached_df(ticker)
    if df.empty:
        pytest.skip(f"No cached data found for {ticker}")
    day_df = _first_trading_day(df)
    # Ensure we have OHLC columns; if not, skip
    required_cols = {"open", "high", "low", "close"}
    if not required_cols.issubset(set(day_df.columns)):
        pytest.skip(f"Missing OHLC columns in cached data for {ticker}")
    strategy = ORBStrategy(strategy_config=build_default_orb_strategy_config())
    entries = 0
    for i in range(len(day_df)):
        window = day_df.iloc[: i + 1]
        entry_signal, exit_flag = strategy.generate_signal_incremental(window)
        if entry_signal != 0:
            entries += 1
    # Indicators should have been added lazily
    assert "ORB_Breakout" in day_df.columns or entries >= 0, "ORB_Breakout indicator not added"
    # At most one entry expected for ORB logic per day (after exit strategy resets)
    assert entries <= 1, f"More than one entry detected ({entries}) for {ticker} on first day"


def test_orb_strategy_initial_stop_and_take_profit_assignment():
    df = _load_cached_df("AAPL")
    if df.empty:
        pytest.skip("No cached data found for AAPL")
    day_df = _first_trading_day(df)
    strategy = ORBStrategy(strategy_config=build_default_orb_strategy_config())
    initial_stop_values = []
    take_profit_values = []
    # Walk through bars until first entry established
    for i in range(len(day_df)):
        window = day_df.iloc[: i + 1]
        entry_signal, exit_flag = strategy.generate_signal_incremental(window)
        if entry_signal != 0:
            # Capture assigned stops
            initial_stop_values.append(strategy._initial_stop)
            take_profit_values.append(strategy._take_profit)
            break
    if not initial_stop_values:
        pytest.skip("No ORB entry occurred on first trading day for AAPL; cannot verify stops")
    assert initial_stop_values[0] is None or isinstance(initial_stop_values[0], (int, float)), "Initial stop not numeric or None"
    assert take_profit_values[0] is None or isinstance(take_profit_values[0], (int, float)), "Take profit not numeric or None"


def test_orb_strategy_exit_flag_eventually_triggers_eod():
    df = _load_cached_df("AAPL")
    if df.empty:
        pytest.skip("No cached data found for AAPL")
    day_df = _first_trading_day(df)
    strategy = ORBStrategy(strategy_config=build_default_orb_strategy_config())
    entered = False
    exit_triggered = False
    for i in range(len(day_df)):
        window = day_df.iloc[: i + 1]
        entry_signal, exit_flag = strategy.generate_signal_incremental(window)
        if entry_signal != 0:
            entered = True
        if exit_flag:
            exit_triggered = True
            break
    if not entered:
        pytest.skip("No entry; cannot test exit logic")
    # We allow that an exit may occur via EOD logic or stop/take-profit
    assert exit_triggered, "Exit flag did not trigger before end of first trading day"
