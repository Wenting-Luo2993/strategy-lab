"""Causal, strategy-independent session VWAP and same-clock relative volume."""

from datetime import time
from typing import Literal

import numpy as np
import pandas as pd


def session_keys(
    df: pd.DataFrame,
    exchange_tz: str,
    session_open: str,
    session_close: str,
) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Validated local bar times and mask for an explicit same-day session."""
    stamps = pd.DatetimeIndex(df["timestamp"] if "timestamp" in df else df.index)
    if stamps.tz is None or stamps.hasnans:
        raise ValueError("Session indicators require timezone-aware, nonmissing timestamps")
    if not stamps.is_monotonic_increasing or not stamps.is_unique:
        raise ValueError("Session indicator timestamps must be sorted and unique")
    start, end = time.fromisoformat(session_open), time.fromisoformat(session_close)
    if start >= end:
        raise ValueError("session_open must precede session_close (same-day sessions)")
    local = stamps.tz_convert(exchange_tz)
    regular = (local.time >= start) & (local.time < end)
    return local, regular


def _volume(df: pd.DataFrame) -> np.ndarray:
    values = df["volume"].to_numpy(dtype=float)
    if np.isinf(values).any() or (values < 0).any():
        raise ValueError("Volume must be nonnegative and finite, or NaN when missing")
    return values


def session_vwap_series(
    df: pd.DataFrame,
    *,
    price_source: Literal["hlc3", "close"] = "hlc3",
    exchange_tz: str = "America/New_York",
    session_open: str = "09:30",
    session_close: str = "16:00",
) -> pd.Series:
    """Bar-price VWAP from session open through each completed bar.

    HLC3 is the bar's typical price, not tick-exact VWAP. Resets each local
    session, excludes extended hours, and never reads later bars. A missing
    price/volume invalidates that session from the missing bar onward.
    """
    if price_source not in {"hlc3", "close"}:
        raise ValueError(f"Unsupported VWAP price_source: {price_source}")
    if df.empty:
        return pd.Series(np.nan, index=df.index, dtype=float)
    local, regular = session_keys(df, exchange_tz, session_open, session_close)
    volume = _volume(df)
    if price_source == "hlc3":
        price = (df["high"] + df["low"] + df["close"]).to_numpy(dtype=float) / 3
    else:
        price = df["close"].to_numpy(dtype=float)
    if np.isinf(price).any():
        raise ValueError("VWAP prices must be finite, or NaN when missing")
    rows = pd.DataFrame({
        "session": local.strftime("%Y-%m-%d")[regular],
        "price": price[regular],
        "volume": volume[regular],
    })
    groups = rows["session"]
    numerator = (rows["price"] * rows["volume"]).groupby(groups).cumsum()
    denominator = rows["volume"].groupby(groups).cumsum()
    missing = rows[["price", "volume"]].isna().any(axis=1).groupby(groups).cummax()
    values = (numerator / denominator.where(denominator > 0)).mask(missing)
    result = pd.Series(np.nan, index=df.index, dtype=float)
    result.iloc[np.flatnonzero(regular)] = values.to_numpy()
    return result


def same_time_relative_volume_series(
    df: pd.DataFrame,
    lookback_sessions: int = 20,
    *,
    exchange_tz: str = "America/New_York",
    session_open: str = "09:30",
    session_close: str = "16:00",
) -> pd.Series:
    """Volume / mean volume of that clock-time bar in prior observed sessions.

    The current session is excluded from the denominator. All preceding
    ``lookback_sessions`` observed regular sessions must contain the slot;
    missing slots and zero denominators yield NaN, not a shortened lookback.
    Input must hold one symbol at one bar resolution.
    """
    if lookback_sessions < 1:
        raise ValueError("lookback_sessions must be positive")
    if df.empty:
        return pd.Series(np.nan, index=df.index, dtype=float)
    local, regular = session_keys(df, exchange_tz, session_open, session_close)
    volume = _volume(df)
    rows = pd.DataFrame({
        "session": local.strftime("%Y-%m-%d")[regular],
        "slot": local.strftime("%H:%M:%S")[regular],
        "volume": volume[regular],
    })
    table = rows.pivot(index="session", columns="slot", values="volume")
    prior = table.shift(1).rolling(
        lookback_sessions, min_periods=lookback_sessions
    ).mean()
    relative = table / prior.where(prior > 0)
    positions = (
        relative.index.get_indexer(rows["session"]),
        relative.columns.get_indexer(rows["slot"]),
    )
    result = pd.Series(np.nan, index=df.index, dtype=float)
    result.iloc[np.flatnonzero(regular)] = relative.to_numpy()[positions]
    return result
