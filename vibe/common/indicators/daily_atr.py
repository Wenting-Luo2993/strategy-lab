"""Daily ATR known at the session open, for intraday strategies.

The value for a session uses only completed prior regular sessions
(09:30-16:00 America/New_York by default). Smoothing is explicitly simple
(the unchanged default) or Wilder, shifted by one session. It never sees the
session it is attached to, so intraday bars can use it from the first bar.
"""

from __future__ import annotations

from datetime import time
from typing import Literal

import numpy as np
import pandas as pd

from vibe.common.indicators.batch import atr_series

EXCHANGE_TZ = "America/New_York"
SESSION_OPEN = pd.Timedelta(hours=9, minutes=30)
SESSION_CLOSE = pd.Timedelta(hours=16)


def daily_atr_column(period: int, smoothing: Literal["simple", "wilder"] = "simple") -> str:
    suffix = "_WILDER" if smoothing == "wilder" else ""
    return f"DAILY_ATR_{period}{suffix}"


def atr_known_at_open(
    daily: pd.DataFrame, period: int = 14, *, smoothing: Literal["simple", "wilder"] = "simple"
) -> pd.Series:
    """Daily ATR known at each session's open, aligned to that session's row.

    ``daily`` holds one row per regular session with ``high``, ``low`` and
    ``close``, in session order.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if smoothing not in {"simple", "wilder"}:
        raise ValueError(f"Unsupported daily ATR smoothing: {smoothing}")
    if daily.empty:
        return pd.Series(dtype=float, index=daily.index)
    if smoothing == "wilder":
        missing = daily[["high", "low", "close"]].isna().any(axis=1)
        if np.isinf(daily[["high", "low", "close"]].to_numpy(dtype=float)).any():
            raise ValueError("Daily ATR prices must be finite, or NaN when missing")
        return atr_series(daily, length=period).mask(missing.cummax()).shift(1)
    prev_close = daily["close"].shift(1)
    true_range = np.maximum.reduce([
        (daily["high"] - daily["low"]).to_numpy(),
        (daily["high"] - prev_close).abs().to_numpy(),
        (daily["low"] - prev_close).abs().to_numpy(),
    ])
    true_range = pd.Series(true_range, index=daily.index).where(prev_close.notna())
    return true_range.rolling(period, min_periods=period).mean().shift(1)


def _bar_timestamps(df: pd.DataFrame, exchange_tz: str = EXCHANGE_TZ) -> pd.DatetimeIndex:
    """Bar times in exchange time.

    Naive times are taken as exchange-local, as ``ORBCalculator`` does; a
    column mixing offsets (e.g. after concatenating feeds) is normalised via UTC.
    """
    if "timestamp" in df.columns:
        column = df["timestamp"]
        if pd.api.types.is_datetime64_any_dtype(column):
            stamps = pd.DatetimeIndex(column)
        else:
            stamps = pd.DatetimeIndex(pd.to_datetime(column, utc=True))
    elif isinstance(df.index, pd.DatetimeIndex):
        stamps = df.index
    else:
        raise ValueError("bars need a 'timestamp' column or a DatetimeIndex")
    if stamps.tz is None:
        return stamps.tz_localize(exchange_tz, ambiguous="NaT", nonexistent="shift_forward")
    return stamps.tz_convert(exchange_tz)


def add_daily_atr(
    df: pd.DataFrame,
    period: int = 14,
    *,
    smoothing: Literal["simple", "wilder"] = "simple",
    exchange_tz: str = EXCHANGE_TZ,
    session_open: str = "09:30",
    session_close: str = "16:00",
) -> pd.DataFrame:
    """Return a copy with the named simple or ``DAILY_ATR_{period}_WILDER`` column.

    Every bar of a session, including pre-market bars, gets that session's
    open-known daily ATR. Sessions without ``period`` completed prior sessions
    get NaN.
    """
    if period < 1 or smoothing not in {"simple", "wilder"}:
        raise ValueError("Daily ATR needs a positive period and simple/wilder smoothing")
    start, end = time.fromisoformat(session_open), time.fromisoformat(session_close)
    if start >= end:
        raise ValueError("Daily ATR requires a same-day session with open before close")
    out = df.copy()
    column = daily_atr_column(period, smoothing)
    if out.empty:
        out[column] = pd.Series(dtype=float)
        return out

    local = _bar_timestamps(out, exchange_tz)
    bar_dates = np.asarray(local.strftime("%Y-%m-%d"))
    regular = np.asarray((local.time >= start) & (local.time < end))

    rth = pd.DataFrame(
        {
            "high": out["high"].to_numpy()[regular],
            "low": out["low"].to_numpy()[regular],
            "close": out["close"].to_numpy()[regular],
            "date": bar_dates[regular],
            "order": np.asarray(local.asi8)[regular],
        }
    ).sort_values("order")
    daily = rth.groupby("date", sort=True).agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
    )
    if smoothing == "wilder":
        missing = rth[["high", "low", "close"]].isna().any(axis=1).groupby(rth["date"]).any()
        daily.loc[missing[missing].index, ["high", "low", "close"]] = np.nan
    atr = atr_known_at_open(daily, period, smoothing=smoothing)
    out[column] = pd.Series(bar_dates).map(atr).to_numpy(dtype=float)
    return out
