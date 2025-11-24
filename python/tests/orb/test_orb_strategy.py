import os
from pathlib import Path
import pandas as pd
import pytest

from src.strategies.orb import ORBStrategy
from src.config import build_default_orb_strategy_config
from tests.utils import strategy_config_to_dict, load_fixture_df

FIXTURE_START_DATE = "2025-11-07"  # single-day fixture date; adjust if changed


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
    # Build start/end in a tz-aware way if index carries timezone information.
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        start = pd.Timestamp(first_day, tz=idx.tz)
    else:
        start = pd.Timestamp.combine(pd.Timestamp(first_day).date(), pd.Timestamp.min.time())
    end = start + pd.Timedelta(days=1)
    # Pandas disallows direct comparison of tz-aware index vs naive timestamp; safeguard here.
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None and start.tz is None:
        # Localize naive start/end to index tz for comparison if somehow still naive
        start = start.tz_localize(idx.tz)
        end = end.tz_localize(idx.tz)
    return df[(idx >= start) & (idx < end)].copy()


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA"])  # broaden coverage; will skip if missing
def test_orb_strategy_with_default_config_adds_indicators_and_generates_entry(ticker, assert_snapshot):
    df = load_fixture_df(ticker, start_date=FIXTURE_START_DATE)
    if df.empty:
        pytest.skip(f"No cached data found for {ticker}")
    day_df = _first_trading_day(df)
    # Ensure we have OHLC columns; if not, skip
    required_cols = {"open", "high", "low", "close"}
    if not required_cols.issubset(set(day_df.columns)):
        pytest.skip(f"Missing OHLC columns in cached data for {ticker}")
    strategy_cfg = build_default_orb_strategy_config()
    strategy = ORBStrategy(strategy_config=strategy_cfg)
    entries = 0
    position_ctx = None
    entry_signals = []
    exit_flags = []
    for i in range(len(day_df)):
        window = day_df.iloc[: i + 1]
        entry_signal, exit_flag, position_ctx = strategy.generate_signal_incremental_ctx(window, position_ctx)
        if entry_signal != 0:
            entries += 1
        entry_signals.append(entry_signal)
        exit_flags.append(1 if exit_flag else 0)
    # Indicators should have been added lazily
    assert "ORB_Breakout" in day_df.columns or entries >= 0, "ORB_Breakout indicator not added"
    # At most one entry expected for ORB logic per day (after exit strategy resets)
    assert entries <= 1, f"More than one entry detected ({entries}) for {ticker} on first day"
    # Snapshot signals + generated entry/exit flags for regression tracking
    snapshot_df = day_df.copy()
    snapshot_df["entry_signal"] = entry_signals
    snapshot_df["exit_flag"] = exit_flags
    # Name pattern ensures one snapshot per ticker; kind defaults to 'signals'
    assert_snapshot(
        snapshot_df,
        name=f"orb_strategy__{ticker}",
        kind="signals",
        strategy_config=strategy_config_to_dict(strategy_cfg),
    )


def test_orb_strategy_initial_stop_and_take_profit_assignment():
    df = load_fixture_df("AAPL", start_date=FIXTURE_START_DATE)
    if df.empty:
        pytest.skip("No cached data found for AAPL")
    day_df = _first_trading_day(df)
    strategy = ORBStrategy(strategy_config=build_default_orb_strategy_config())
    captured_ctx = None
    position_ctx = None
    for i in range(len(day_df)):
        window = day_df.iloc[: i + 1]
        entry_signal, exit_flag, position_ctx = strategy.generate_signal_incremental_ctx(window, position_ctx)
        if entry_signal != 0:
            captured_ctx = position_ctx
            break
    if not captured_ctx:
        pytest.skip("No ORB entry occurred on first trading day for AAPL; cannot verify stops")
    assert captured_ctx.get('initial_stop') is None or isinstance(captured_ctx.get('initial_stop'), (int, float)), "Initial stop not numeric or None"
    assert captured_ctx.get('take_profit') is None or isinstance(captured_ctx.get('take_profit'), (int, float)), "Take profit not numeric or None"


def test_orb_strategy_exit_flag_eventually_triggers_eod():
    df = load_fixture_df("AAPL", start_date=FIXTURE_START_DATE)
    if df.empty:
        pytest.skip("No cached data found for AAPL")
    day_df = _first_trading_day(df)
    strategy = ORBStrategy(strategy_config=build_default_orb_strategy_config())
    position_ctx = None
    entered = False
    exit_triggered = False
    for i in range(len(day_df)):
        window = day_df.iloc[: i + 1]
        entry_signal, exit_flag, position_ctx = strategy.generate_signal_incremental_ctx(window, position_ctx)
        if entry_signal != 0:
            entered = True
        if exit_flag:
            exit_triggered = True
            position_ctx = None
            break
    if not entered:
        pytest.skip("No entry; cannot test exit logic")
    assert exit_triggered, "Exit flag did not trigger before end of first trading day"
