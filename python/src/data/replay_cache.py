"""Data replay loader.

Extends CacheDataLoader to support incremental (row-by-row) revelation of the
most recent trading day to simulate a live feed during backtests/dark trading.

Design:
  * On first fetch per (symbol,timeframe), we pull the full historical dataset
    via the parent loader (super().fetch). We split it into `history` (all rows
    strictly before the last date) and `replay_day` (rows from the final date).
  * We keep an internal pointer of how many rows from `replay_day` have been
    revealed so far per symbol. Subsequent fetch calls only return up to that
    revealed slice, creating the illusion of progressive data arrival.
  * `advance(n)` increases the revealed count by `n * reveal_increment` (bounded
    by remaining rows).
  * Optional start offset: begin the replay with bars up to
    (market_open - offset_minutes) already revealed; this allows simulation
    starting X minutes before open.

Assumptions / Simplifications:
  * Source data index is tz-aware UTC. Market open time is provided in a
    specific timezone (default America/New_York); we convert it to UTC when
    computing the initial reveal threshold.
  * We do not (yet) handle DST edge cases beyond standard pytz conversion.
  * Incremental revelation occurs only on the last date in the dataset; earlier
    historical dates are fully available immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Dict, Optional
import pytz
import pandas as pd

from .cache import CacheDataLoader
from src.utils.logger import get_logger

# Module-level logger
logger = get_logger("ReplayCache")


@dataclass
class ReplayState:
    history: pd.DataFrame            # All rows before replay day
    replay_day: pd.DataFrame         # Rows for final date
    revealed_rows: int               # Count of rows currently revealed
    total_rows: int                  # Total rows in replay_day


class DataReplayCacheDataLoader(CacheDataLoader):
    """Cache-only replay loader revealing last day incrementally.

    This variant intentionally does NOT accept or use an underlying live
    DataLoader. It will only serve data that already exists inside the local
    cache directory. If the required parquet cache files do not exist the
    replay will produce empty frames.
    """

    def __init__(
        self,
        market_open: time,
        timezone: str = "America/New_York",
        start_offset_minutes: int = 0,
        reveal_increment: int = 1,
        **kwargs,
    ):
        # Force cache-only mode: wrapped_loader=None
        super().__init__(wrapped_loader=None, **kwargs)
        self.market_open = market_open
        self.timezone = timezone
        self.start_offset_minutes = start_offset_minutes
        self.reveal_increment = reveal_increment
        # Per symbol+timeframe replay state
        self._state: Dict[str, ReplayState] = {}

    def _state_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}::{timeframe}"

    def _initialize_state(self, symbol: str, timeframe: str) -> None:
        key = self._state_key(symbol, timeframe)
        if key in self._state:
            logger.debug(f"Replay state already initialized for {key}")
            return
        # Fetch full data from cache only (start/end None -> parent clamps)
        full_df = super().fetch(symbol, timeframe, start=None, end=None)
        if full_df is None or full_df.empty:
            logger.warning(f"Replay initialization found no data for {symbol} {timeframe}; creating empty state")
            self._state[key] = ReplayState(pd.DataFrame(), pd.DataFrame(), 0, 0)
            return

        # Identify replay day (final date) using safe date extraction
        idx = full_df.index
        last_date = idx[-1].date()
        all_dates = [ts.date() for ts in idx]
        mask_replay = [d == last_date for d in all_dates]
        mask_history = [d < last_date for d in all_dates]
        replay_day_df = full_df[mask_replay]
        history_df = full_df[mask_history]
        logger.info(
            f"Initialized replay state for {symbol} {timeframe}: history_rows={len(history_df)} replay_day_rows={len(replay_day_df)} last_date={last_date}"
        )
        # Preserve original timezone (e.g., US/Eastern) so ORB start_time comparisons align
        # Avoid converting to UTC here; indicator logic expects session-local times.

        # Determine initial revealed rows.
        # Previous implementation assumed UTC-indexed data and added the offset AFTER market open,
        # which caused zero rows to be revealed when data is already localized (e.g. America/New_York).
        # New logic: always interpret the index's timezone (or configured market timezone if naive)
        # and reveal up to (market_open) inclusive. If start_offset_minutes > 0 we ALSO include the
        # preceding offset minutes of pre-market bars (i.e. market_open - offset). This means the
        # first fetch will have at least the opening bar.
        revealed_rows = 0
        tz_index = replay_day_df.index.tz
        if tz_index is None:
            tz_index = pytz.timezone(self.timezone)
        # Determine opening timestamp in the SAME timezone as the data index (do NOT shift to UTC)
        # Tests build synthetic data already localized to UTC and expect a direct comparison of 09:30 == 09:30.
        # Previous implementation converted 09:30 America/New_York -> 13:30 UTC causing the entire replay day
        # to be considered "pre-open" and fully revealed. Here we localize using the index tz directly.
        idx_tz = replay_day_df.index.tz or pytz.timezone(self.timezone)
        open_dt = idx_tz.localize(datetime.combine(last_date, self.market_open))

        if self.start_offset_minutes > 0:
            threshold_dt = open_dt - timedelta(minutes=self.start_offset_minutes)
            logger.debug(
                f"Replay threshold adjusted by offset {self.start_offset_minutes}m: threshold={threshold_dt} open={open_dt}"
            )
        else:
            threshold_dt = open_dt

        # Select bars from threshold_dt up to and including the opening bar. If there are no pre-market bars
        # (common in tests) this yields exactly one bar.
        initial_slice = replay_day_df[(replay_day_df.index >= threshold_dt) & (replay_day_df.index <= open_dt)]
        # Always guarantee at least the opening bar if it exists
        if initial_slice.empty:
            opening_bar = replay_day_df[replay_day_df.index == open_dt]
            initial_slice = opening_bar
        revealed_rows = len(initial_slice)
        logger.info(
            f"Initial replay reveal for {symbol} {timeframe}: revealed_rows={revealed_rows} of replay_day_rows={len(replay_day_df)}"
        )
        # Cap at total rows
        revealed_rows = min(revealed_rows, len(replay_day_df))

        state = ReplayState(
            history=history_df,
            replay_day=replay_day_df,
            revealed_rows=revealed_rows,
            total_rows=len(replay_day_df),
        )
        self._state[key] = state

    def advance(self, symbol: Optional[str] = None, timeframe: Optional[str] = None, n: int = 1) -> None:
        """Advance replay by n * reveal_increment rows (bounded).

        If symbol/timeframe unspecified, advance all tracked states.
        """
        targets = []
        if symbol and timeframe:
            targets.append(self._state_key(symbol, timeframe))
        else:
            targets = list(self._state.keys())
        logger.debug(f"Advance called: n={n} increment={self.reveal_increment} targets={targets}")
        for key in targets:
            state = self._state.get(key)
            if not state:
                logger.debug(f"Advance skipped; no state for {key}")
                continue
            add_rows = n * self.reveal_increment
            state.revealed_rows = min(state.revealed_rows + add_rows, state.total_rows)
            logger.info(
                f"Replay advanced for {key}: +{add_rows} rows (requested), now revealed={state.revealed_rows}/{state.total_rows}"
            )

    def fetch(self, symbol: str, timeframe: str, start=None, end=None) -> pd.DataFrame:  # type: ignore[override]
        """Return combined history plus currently revealed replay rows.

        Args mirror parent but are intentionally ignored for replay progression;
        the full dataset was already loaded on initialization.
        """
        self._initialize_state(symbol, timeframe)
        key = self._state_key(symbol, timeframe)
        state = self._state[key]
        if state.total_rows == 0:
            logger.debug(f"Fetch returning empty replay day for {key}")
            return state.history  # empty
        revealed_slice = state.replay_day.iloc[: state.revealed_rows]
        combined = pd.concat([state.history, revealed_slice], axis=0)
        logger.debug(
            f"Fetch for {key}: history_rows={len(state.history)} revealed_rows={state.revealed_rows} total_replay_rows={state.total_rows} combined_rows={len(combined)}"
        )
        return combined.sort_index()

    def replay_progress(self, symbol: str, timeframe: str) -> float:
        """Return fraction (0..1) of replay day revealed."""
        key = self._state_key(symbol, timeframe)
        state = self._state.get(key)
        if not state or state.total_rows == 0:
            logger.debug(f"Replay progress requested for {key} but no state or empty replay day")
            return 0.0
        progress = state.revealed_rows / state.total_rows
        logger.debug(f"Replay progress for {key}: {progress:.4f}")
        return progress
