"""Strategy Verification Script

This script replays cached historical data for selected tickers and timeframes, applies
ORBStrategy incrementally (using generate_signal_incremental), appends the generated
entry signals and exit flags to the OHLCV DataFrame, saves the enriched DataFrame(s)
as CSV under results/strategy-verification, and produces a plot similar to visual_scripts.py.

Usage (from project root or python folder after venv activation):
    python strategy_verification.py --tickers AAPL MSFT --timeframe 1Min --run-id verify01

If no tickers are specified it will attempt to infer from available cache folders.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import pandas as pd

# Local imports (assumes running inside python/ working directory or added to PYTHONPATH)
from src.data.replay_cache import DataReplayCacheDataLoader
from src.strategies.orb import ORBStrategy
from src.visualization.signal_plots import plot_signals_for_tickers
from src.utils.logger import get_logger
from src.config import build_orb_atr_strategy_config_with_or_stop
from datetime import datetime, date
from datetime import time as dt_time
from types import SimpleNamespace

logger = get_logger("StrategyVerification")

RESULTS_SUBDIR = Path("python/results/strategy-verification")
RESULTS_SUBDIR.mkdir(parents=True, exist_ok=True)


def verify_replay(strategy: ORBStrategy, loader: DataReplayCacheDataLoader, ticker: str, timeframe: str, verbose: bool = False) -> pd.DataFrame:
    """Replay data incrementally using DataReplayCacheDataLoader, collecting signals per revealed bar.

    Mimics orchestrator cycle progression: each advance reveals one additional bar from the final day.

    Returns:
        Enriched DataFrame with strategy_signal & strategy_exit_flag columns for all revealed bars (history + replay day).
    """
    # Initial fetch (history + initial revealed slice)
    df_current = loader.fetch(ticker, timeframe)
    logger.info("replay.start", extra={"meta": {"ticker": ticker, "initial_rows": len(df_current)}})
    signals: Dict[pd.Timestamp, int] = {}
    exits: Dict[pd.Timestamp, int] = {}
    processed = 0
    # Process already revealed bars
    entry_count = 0
    exit_count = 0
    while True:
        current_len = len(df_current)
        if current_len > processed:
            for i in range(processed, current_len):
                window = df_current.iloc[: i + 1]
                entry_signal, exit_flag = strategy.generate_signal_incremental(window)
                ts = window.index[-1]
                signals[ts] = entry_signal
                exits[ts] = 1 if exit_flag else 0
                if entry_signal != 0:
                    entry_count += 1
                    if verbose:
                        logger.info("signal.entry", extra={"meta": {"ticker": ticker, "bar": str(ts), "signal": entry_signal}})
                if exit_flag:
                    exit_count += 1
                    if verbose:
                        logger.info("signal.exit_flag", extra={"meta": {"ticker": ticker, "bar": str(ts)}})
            processed = current_len
        # Break if replay complete
        progress = loader.replay_progress(ticker, timeframe)
        if progress >= 1.0:
            break
        # Advance one increment and fetch again
        loader.advance(symbol=ticker, timeframe=timeframe, n=1)
        df_current = loader.fetch(ticker, timeframe)
        if verbose:
            logger.info("replay.advance", extra={"meta": {"ticker": ticker, "progress_pct": round(progress * 100, 2), "rows": len(df_current)}})
    # Build enriched DataFrame
    enriched = df_current.copy()
    enriched['strategy_signal'] = [signals.get(ts, 0) for ts in enriched.index]
    enriched['strategy_exit_flag'] = [exits.get(ts, 0) for ts in enriched.index]
    logger.info("replay.complete", extra={"meta": {"ticker": ticker, "final_rows": len(enriched), "entries": entry_count, "exits": exit_count}})
    return enriched


def incremental_apply(strategy: ORBStrategy, df: pd.DataFrame) -> pd.DataFrame:
    """Apply strategy incrementally over DataFrame, recording entries & exits.

    Adds two new columns:
      strategy_signal: 1 for long entry, -1 for short entry, 0 otherwise (only on entry bar)
      strategy_exit_flag: 1 on bars where the strategy signalled an exit_flag True, else 0
    """
    sig_col = []
    exit_col = []
    for i in range(len(df)):
        window = df.iloc[: i + 1]
        entry_signal, exit_flag = strategy.generate_signal_incremental(window)
        # We record only the entry signal (not opposite exit) per design
        sig_col.append(entry_signal)
        exit_col.append(1 if exit_flag else 0)
    df_out = df.copy()
    df_out["strategy_signal"] = sig_col
    df_out["strategy_exit_flag"] = exit_col
    return df_out


def save_dataframe(df: pd.DataFrame, ticker: str, run_id: str) -> Path:
    """Persist enriched per-day ticker DataFrame (optional)."""
    out_path = RESULTS_SUBDIR / f"{ticker}_{run_id}.csv"
    df.to_csv(out_path, index=True)
    logger.info("saved.csv", extra={"meta": {"ticker": ticker, "rows": len(df), "path": str(out_path)}})
    return out_path


def build_trades_csv_from_signals(*args, **kwargs):  # Deprecated placeholder
    logger.debug("trades.csv.skip", extra={"meta": {"reason": "No longer needed for plotting."}})
    return Path("")


def append_diagnostics(*args, **kwargs):  # Deprecated placeholder
    logger.debug("diagnostics.csv.skip", extra={"meta": {"reason": "No longer needed for plotting."}})


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Strategy verification incremental replay (multi-day)")
    p.add_argument("--tickers", nargs="*", help="Tickers to verify (defaults to those in cache)")
    p.add_argument("--timeframe", default="5m", help="Timeframe (must be 5m)")
    p.add_argument("--run-id", default="strategy_verify", help="Base run identifier for output grouping")
    p.add_argument("--orb-window", type=int, default=5, help="ORB breakout window (minutes)")
    p.add_argument("--initial-stop-pct", type=float, default=0.25, help="Initial stop percentage of OR range (0-1)")
    p.add_argument("--verbose", action="store_true", help="Enable verbose per-bar logging")
    p.add_argument("--days", type=int, default=10, help="Number of most recent trading days to verify if date range not supplied")
    p.add_argument("--date-start", type=str, help="Optional inclusive start date (YYYY-MM-DD)")
    p.add_argument("--date-end", type=str, help="Optional inclusive end date (YYYY-MM-DD); defaults to today if start given")
    return p.parse_args()


def discover_cached_tickers(cache_root: Path, timeframe: str) -> List[str]:
    tickers = []
    if not cache_root.exists():
        return tickers
    # Expect structure like data_cache/{ticker}/{timeframe}.csv OR subfolder
    for child in cache_root.iterdir():
        if child.is_dir():
            # Look for timeframe matches inside directory
            for inner in child.iterdir():
                if timeframe.lower() in inner.name.lower():
                    tickers.append(child.name)
                    break
    return sorted(tickers)


def _extract_trading_days(df: pd.DataFrame) -> List[date]:
    if df.empty:
        return []
    # Normalize to naive date for grouping
    if df.index.tz is not None:
        dates = [ts.tz_convert('UTC').date() for ts in df.index]  # ensure stable
    else:
        dates = [ts.date() for ts in df.index]
    unique = sorted(set(dates))
    return unique


def _slice_day(df: pd.DataFrame, day: date) -> pd.DataFrame:
    if df.empty:
        return df
    # Preserve timezone awareness if present
    if df.index.tz is not None:
        start = pd.Timestamp(day.year, day.month, day.day, tz=df.index.tz)
        end = start + pd.Timedelta(days=1)
    else:
        start = pd.Timestamp(day.year, day.month, day.day)
        end = start + pd.Timedelta(days=1)
    return df[(df.index >= start) & (df.index < end)].copy()


def _fetch_full_history(loader: DataReplayCacheDataLoader, ticker: str, timeframe: str) -> pd.DataFrame:
    """Reveal full cached history for ticker/timeframe.

    Original implementation could hang if replay_progress never advanced
    (e.g., missing cache files or loader internal state not initialized).

    We first perform an initial fetch to seed internal state, then advance
    with guarded iterations. If progress doesn't move after several advances
    we break and return what we have (allowing graceful degradation).
    """
    # Initial seed fetch
    try:
        df_initial = loader.fetch(ticker, timeframe)
    except Exception as e:
        logger.warning("history.seed.failed", extra={"meta": {"ticker": ticker, "error": str(e)}})
        return pd.DataFrame()

    max_iterations = 40  # safety cap
    last_progress = loader.replay_progress(ticker, timeframe)
    if last_progress >= 1.0:
        return df_initial

    for i in range(max_iterations):
        loader.advance(symbol=ticker, timeframe=timeframe, n=500)
        current_progress = loader.replay_progress(ticker, timeframe)
        if current_progress >= 1.0:
            break
        last_progress = current_progress
        if i % 5 == 0:
            logger.info("history.progress", extra={"meta": {"ticker": ticker, "progress_pct": round(current_progress * 100, 2)}})

    try:
        return loader.fetch(ticker, timeframe)
    except Exception as e:
        logger.warning("history.final.fetch.failed", extra={"meta": {"ticker": ticker, "error": str(e)}})
        return df_initial


def main():
    args = parse_args()
    cache_root = Path("data_cache")
    if not args.tickers:
        inferred = discover_cached_tickers(cache_root, args.timeframe)
        if not inferred:
            raise SystemExit("No tickers provided and none inferred from cache")
        tickers = inferred
    else:
        tickers = args.tickers

    logger.info("verification.start", extra={"meta": {"tickers": tickers, "timeframe": args.timeframe, "run_id": args.run_id}})

    # Instantiate components
    if args.timeframe.lower() not in ('5m', '5min'):
        raise SystemExit("Only 5m timeframe supported in verification script.")
    timeframe = '5m'
    # DataReplay loader (cache only)
    loader = DataReplayCacheDataLoader(
        market_open=dt_time(9, 30),
        timezone='America/New_York',
        start_offset_minutes=0,
        reveal_increment=1,
        cache_dir=str(cache_root)
    )
    # Build a default ORB + ATR strategy configuration (centralized factory)
    strategy_config = build_orb_atr_strategy_config_with_or_stop(initial_stop_orb_pct=args.initial_stop_pct)
    logger.info("strategy.config", extra={"meta": {"run_id": args.run_id, "orb_timeframe": strategy_config.orb_config.timeframe, "orb_start": strategy_config.orb_config.start_time, "body_breakout_pct": strategy_config.orb_config.body_breakout_percentage, "stop_loss_type": strategy_config.risk.stop_loss_type, "stop_loss_value": strategy_config.risk.stop_loss_value, "take_profit_type": strategy_config.risk.take_profit_type, "take_profit_value": strategy_config.risk.take_profit_value, "initial_stop_orb_pct": strategy_config.risk.initial_stop_orb_pct}})
    # Diagnostics CSV deprecated; plotting uses in-memory frames now.

    # Multi-day processing
    per_day_images: Dict[str, List[str]] = {}
    # Pre-fetch full history per ticker
    history_map: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            full_df = _fetch_full_history(loader, t, timeframe)
            history_map[t] = full_df
            logger.info("history.loaded", extra={"meta": {"ticker": t, "rows": len(full_df)}})
        except Exception as e:
            logger.warning("history.load.failed", extra={"meta": {"ticker": t, "error": str(e)}})

    # Determine day range
    all_days: List[date] = []
    for t, df in history_map.items():
        all_days.extend(_extract_trading_days(df))
    if not all_days:
        logger.warning("no.days.found", extra={"meta": {"tickers": tickers}})
        return
    unique_days = sorted(set(all_days))
    # Filter by provided dates or take last N
    if args.date_start:
        start_d = datetime.strptime(args.date_start, "%Y-%m-%d").date()
        end_d = datetime.strptime(args.date_end, "%Y-%m-%d").date() if args.date_end else unique_days[-1]
        day_range = [d for d in unique_days if start_d <= d <= end_d]
    else:
        day_range = unique_days[-args.days:]

    try:
        logger.info("day.range.selected", extra={"meta": {"count": len(day_range), "first": str(day_range[0]), "last": str(day_range[-1])}})
    except Exception:
        logger.info("day.range.selected", extra={"meta": {"count": len(day_range)}})

    for day in day_range:
        daily_run_id = f"{args.run_id}_{day.strftime('%Y%m%d')}"
        # No per-day diagnostics/trades cleanup needed now.
        per_day_images[daily_run_id] = []
        logger.info("day.start", extra={"meta": {"run_id": daily_run_id, "day": str(day)}})
        # Process each ticker for this day
        day_data_map: Dict[str, pd.DataFrame] = {}
        for t in tickers:
            df_full = history_map.get(t, pd.DataFrame())
            df_day = _slice_day(df_full, day)
            if df_day.empty:
                logger.info("day.no.data", extra={"meta": {"ticker": t, "day": str(day)}})
                continue
            # New strategy instance to reset internal position state per day & ticker
            strat = ORBStrategy(breakout_window=args.orb_window, strategy_config=strategy_config)
            # Provide market hours for EOD exit logic (close at 16:00 ET)
            strat.market_hours = SimpleNamespace(close_time=dt_time(16, 0))
            enriched_df = incremental_apply(strat, df_day)
            save_dataframe(enriched_df, t, daily_run_id)
            # Store for direct plotting
            day_data_map[t] = enriched_df
        # Plot once per day (aggregated tickers via shared run id)
        try:
            plot_paths = plot_signals_for_tickers(
                data_map=day_data_map,
                output_dir=str(RESULTS_SUBDIR),
                style="candlestick",
                show=False,
                run_id=daily_run_id,
            )
            per_day_images[daily_run_id] = plot_paths
            logger.info("day.plots.saved", extra={"meta": {"run_id": daily_run_id, "images": len(plot_paths)}})
        except Exception as e:
            logger.warning("day.plot.failed", extra={"meta": {"run_id": daily_run_id, "error": str(e)}})
        logger.info("day.complete", extra={"meta": {"run_id": daily_run_id}})

    logger.info("verification.multi_day.complete", extra={"meta": {"days_processed": len(day_range), "run_ids": list(per_day_images.keys())}})


if __name__ == "__main__":
    main()
