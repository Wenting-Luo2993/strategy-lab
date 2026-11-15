"""Causal, dimensionless close-path efficiency for one symbol per input."""

import numpy as np
import pandas as pd

from vibe.common.indicators.session import session_keys


def session_efficiency_ratio(
    bars: pd.DataFrame,
    changes: int = 6,
    *,
    bar_minutes: int = 5,
    exchange_tz: str = "America/New_York",
    session_open: str = "09:30",
    session_close: str = "16:00",
) -> pd.Series:
    """Absolute net close change / absolute close-path length, in [0, 1].

    Requires ``changes + 1`` closes at contiguous ``bar_minutes`` stamps in
    the same local regular session. No gaps or overnight changes are bridged.
    NaN prices invalidate windows containing them; a wholly flat path is 0.
    The value at a start-stamped bar is available only at that bar's close.
    This batch calculation does not opt any strategy or live consumer in.
    """
    for name, value in (("changes", changes), ("bar_minutes", bar_minutes)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    local, regular = session_keys(bars, exchange_tz, session_open, session_close)
    close = bars["close"].to_numpy(dtype=float)
    if np.isinf(close).any() or (close <= 0).any():
        raise ValueError("Efficiency prices must be positive finite values or NaN")
    result = pd.Series(np.nan, index=bars.index, dtype=float)
    if not regular.any():
        return result
    stamps = local[regular]
    rows = pd.DataFrame({
        "close": close[regular],
        "day": stamps.strftime("%Y-%m-%d"),
        "stamp": stamps,
    })
    grouped = rows.groupby("day", sort=False)
    adjacent = grouped["stamp"].diff() == pd.Timedelta(minutes=bar_minutes)
    steps = grouped["close"].diff().abs().where(adjacent)
    path = steps.groupby(rows["day"]).transform(
        lambda s: s.rolling(changes, min_periods=changes).sum()
    )
    displacement = (rows["close"] - grouped["close"].shift(changes)).abs()
    efficiency = (displacement / path.where(path > 0)).mask(path == 0, 0.0)
    result.iloc[np.flatnonzero(regular)] = efficiency.clip(0, 1).to_numpy()
    return result
