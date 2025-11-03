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
            return
        # Fetch full data from cache only (start/end None -> parent clamps)
        full_df = super().fetch(symbol, timeframe, start=None, end=None)
        if full_df is None or full_df.empty:
            self._state[key] = ReplayState(pd.DataFrame(), pd.DataFrame(), 0, 0)
            return

        # Identify replay day (final date)
        last_date = full_df.index[-1].date()
        replay_day_df = full_df[full_df.index.date == last_date]
        history_df = full_df[full_df.index.date < last_date]

        # Determine initial revealed rows based on start offset logic
        revealed_rows = 0
        if self.start_offset_minutes > 0:
            # Heuristic: if the first bar of replay day already matches market_open hh:mm
            # in UTC, we assume data timestamps are 'local' but stored as UTC (typical in tests),
            # so we apply the offset directly in UTC. Otherwise, we convert from the provided
            # market timezone to UTC.
            first_bar = replay_day_df.index.min()
            if first_bar.hour == self.market_open.hour and first_bar.minute == self.market_open.minute:
                open_dt_utc = pytz.UTC.localize(datetime.combine(last_date, self.market_open))
                threshold_utc = open_dt_utc - timedelta(minutes=self.start_offset_minutes)
            else:
                tz = pytz.timezone(self.timezone)
                open_dt_local = tz.localize(datetime.combine(last_date, self.market_open))
                threshold_local = open_dt_local - timedelta(minutes=self.start_offset_minutes)
                threshold_utc = threshold_local.astimezone(pytz.UTC)
            pre_rows = replay_day_df[replay_day_df.index <= threshold_utc]
            revealed_rows = len(pre_rows)

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
        for key in targets:
            state = self._state.get(key)
            if not state:
                continue
            add_rows = n * self.reveal_increment
            state.revealed_rows = min(state.revealed_rows + add_rows, state.total_rows)

    def fetch(self, symbol: str, timeframe: str, start=None, end=None) -> pd.DataFrame:  # type: ignore[override]
        """Return combined history plus currently revealed replay rows.

        Args mirror parent but are intentionally ignored for replay progression;
        the full dataset was already loaded on initialization.
        """
        self._initialize_state(symbol, timeframe)
        key = self._state_key(symbol, timeframe)
        state = self._state[key]
        if state.total_rows == 0:
            return state.history  # empty
        revealed_slice = state.replay_day.iloc[: state.revealed_rows]
        combined = pd.concat([state.history, revealed_slice], axis=0)
        return combined.sort_index()

    def replay_progress(self, symbol: str, timeframe: str) -> float:
        """Return fraction (0..1) of replay day revealed."""
        key = self._state_key(symbol, timeframe)
        state = self._state.get(key)
        if not state or state.total_rows == 0:
            return 0.0
        return state.revealed_rows / state.total_rows
