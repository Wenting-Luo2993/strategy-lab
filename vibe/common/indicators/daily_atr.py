"""Daily ATR known at the session open, for intraday strategies.

The value for a session uses only completed prior regular sessions
(09:30-16:00 America/New_York): the simple mean of the last ``period`` daily
true ranges, shifted by one session. It never sees the session it is attached
to, so intraday bars can use it from the first bar of the day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EXCHANGE_TZ = "America/New_York"
SESSION_OPEN = pd.Timedelta(hours=9, minutes=30)
SESSION_CLOSE = pd.Timedelta(hours=16)


def daily_atr_column(period: int) -> str:
    return f"DAILY_ATR_{period}"


def atr_known_at_open(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """Daily ATR known at each session's open, aligned to that session's row.

    ``daily`` holds one row per regular session with ``high``, ``low`` and
    ``close``, in session order.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    prev_close = daily["close"].shift(1)
    true_range = np.maximum.reduce([
        (daily["high"] - daily["low"]).to_numpy(),
        (daily["high"] - prev_close).abs().to_numpy(),
        (daily["low"] - prev_close).abs().to_numpy(),
    ])
    true_range = pd.Series(true_range, index=daily.index).where(prev_close.notna())
    return true_range.rolling(period, min_periods=period).mean().shift(1)


def _bar_timestamps(df: pd.DataFrame) -> pd.DatetimeIndex:
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
        return stamps.tz_localize(EXCHANGE_TZ, ambiguous="NaT", nonexistent="shift_forward")
    return stamps.tz_convert(EXCHANGE_TZ)


def add_daily_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Return a copy of intraday ``df`` with a ``DAILY_ATR_{period}`` column.

    Every bar of a session, including pre-market bars, gets that session's
    open-known daily ATR. Sessions without ``period`` completed prior sessions
    get NaN.
    """
    out = df.copy()
    column = daily_atr_column(period)
    if out.empty:
        out[column] = pd.Series(dtype=float)
        return out

    local = _bar_timestamps(out)
    since_midnight = local - local.normalize()
    bar_dates = np.asarray(local.strftime("%Y-%m-%d"))
    regular = np.asarray((since_midnight >= SESSION_OPEN) & (since_midnight < SESSION_CLOSE))

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
    atr = atr_known_at_open(daily, period)
    out[column] = pd.Series(bar_dates).map(atr).to_numpy(dtype=float)
    return out
