"""Tick-level random-walk sessions with a known edge (or none)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

BARS_PER_SESSION_5MIN = 78
GAP_SD_TICKS = 20.0


def _weekdays(start: date, n: int) -> list[date]:
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def tick_walk_sessions(
    *,
    n_sessions: int,
    seed: int,
    start: date = date(2019, 1, 2),
    bar_minutes: int = 5,
    start_price: float = 100.0,
    tick: float = 0.01,
    steps_per_bar: int = 225,
    up_probability_with_trend: float = 0.5,
) -> pd.DataFrame:
    """Weekday sessions 09:30-16:00 America/New_York as OHLCV bars.

    Every step moves exactly one tick, so every price between a bar's low and
    high really traded. Each session draws a direction d in {+1, -1}; a step
    follows d with probability ``up_probability_with_trend`` (0.5 is a
    martingale). Each session opens at ``start_price`` plus a random gap, which
    keeps the price positive over very long runs; the gap is independent of
    the session's direction.
    """
    if n_sessions <= 0:
        raise ValueError("n_sessions must be positive")
    if not 0.0 <= up_probability_with_trend <= 1.0:
        raise ValueError("up_probability_with_trend must be in [0, 1]")
    n_bars = 390 // bar_minutes
    rng = np.random.default_rng(seed)
    start_ticks = int(round(start_price / tick))
    frames = []
    for day in _weekdays(start, n_sessions):
        open_ticks = start_ticks + int(round(rng.normal(0.0, GAP_SD_TICKS)))
        direction = 1 if rng.random() < 0.5 else -1
        p_up = (
            up_probability_with_trend
            if direction > 0
            else 1.0 - up_probability_with_trend
        )
        steps = np.where(rng.random(n_bars * steps_per_bar) < p_up, 1, -1)
        path = (open_ticks + np.cumsum(steps)).reshape(n_bars, steps_per_bar)
        close = path[:, -1]
        bar_open = np.concatenate([[open_ticks], close[:-1]])
        high = np.maximum(path.max(axis=1), bar_open)
        low = np.minimum(path.min(axis=1), bar_open)
        index = pd.date_range(
            datetime(day.year, day.month, day.day, 9, 30),
            periods=n_bars,
            freq=f"{bar_minutes}min",
            tz="America/New_York",
        )
        frames.append(
            pd.DataFrame(
                {
                    "open": bar_open * tick,
                    "high": high * tick,
                    "low": low * tick,
                    "close": close * tick,
                    "volume": 100_000.0,
                },
                index=index,
            )
        )
    return pd.concat(frames).round(2)
