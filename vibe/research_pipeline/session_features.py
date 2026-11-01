"""Per-session market-condition features that are known by the end of the opening range.

Every value uses only completed prior sessions plus the opening-range bars of
the session itself. Rows without enough history are NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EXCHANGE_TZ = "America/New_York"
SESSION_OPEN = pd.Timedelta(hours=9, minutes=30)
SESSION_CLOSE = pd.Timedelta(hours=16)
ATR_WINDOW = 14
ATR_PERCENTILE_WINDOW = 252
VOLUME_WINDOW = 20
TREND_LOOKBACK = 21

FEATURE_COLUMNS = (
    "gap_pct",
    "abs_gap_pct",
    "or_width_atr",
    "atr_pctile",
    "rel_open_volume",
    "prior_trend_20d_pct",
)


def build_session_features(bars: pd.DataFrame, *, or_minutes: int = 5) -> pd.DataFrame:
    """Return one row per regular session, indexed by ``YYYY-MM-DD``.

    ``bars`` is intraday OHLCV with a tz-aware index. The opening range is the
    bars stamped in ``[09:30, 09:30 + or_minutes)``.
    """
    if or_minutes <= 0:
        raise ValueError("or_minutes must be positive")
    if bars.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS, dtype=float)

    local = bars.copy()
    local.index = local.index.tz_convert(EXCHANGE_TZ)
    local = local.sort_index()
    since_midnight = local.index - local.index.normalize()
    local = local[(since_midnight >= SESSION_OPEN) & (since_midnight < SESSION_CLOSE)]
    since_midnight = local.index - local.index.normalize()
    dates = local.index.strftime("%Y-%m-%d")
    or_end = SESSION_OPEN + pd.Timedelta(minutes=or_minutes)
    in_or = (since_midnight < or_end)

    n = len(set(dates))
    sessions = sorted(set(dates))
    daily = local.groupby(dates).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reindex(sessions)
    opening = local[in_or].groupby(dates[in_or]).agg(
        or_high=("high", "max"), or_low=("low", "min"), or_volume=("volume", "sum")
    ).reindex(sessions)

    prev_close = daily["close"].shift(1)
    true_range = np.maximum.reduce([
        (daily["high"] - daily["low"]).to_numpy(),
        (daily["high"] - prev_close).abs().to_numpy(),
        (daily["low"] - prev_close).abs().to_numpy(),
    ])
    true_range = pd.Series(true_range, index=daily.index).where(prev_close.notna())

    # ATR known at the close of the previous session, aligned to today's row.
    atr_known = true_range.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean().shift(1)

    gap_pct = (daily["open"] - prev_close) / prev_close * 100.0
    or_width_atr = (opening["or_high"] - opening["or_low"]) / atr_known
    prior_volume_mean = (
        opening["or_volume"].shift(1).rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW).mean()
    )
    rel_open_volume = opening["or_volume"] / prior_volume_mean
    trend = (prev_close - daily["close"].shift(TREND_LOOKBACK)) / daily["close"].shift(
        TREND_LOOKBACK
    ) * 100.0

    atr_values = atr_known.to_numpy()
    atr_pctile = np.full(n, np.nan)
    for i in range(n):
        if i < ATR_PERCENTILE_WINDOW:
            continue
        window = atr_values[i - ATR_PERCENTILE_WINDOW : i]
        today = atr_values[i]
        if np.isnan(today) or np.isnan(window).any():
            continue
        atr_pctile[i] = float((window <= today).mean() * 100.0)

    out = pd.DataFrame(
        {
            "gap_pct": gap_pct,
            "abs_gap_pct": gap_pct.abs(),
            "or_width_atr": or_width_atr,
            "atr_pctile": atr_pctile,
            "rel_open_volume": rel_open_volume,
            "prior_trend_20d_pct": trend,
        },
        index=pd.Index(sessions, name="session"),
    )
    return out.replace([np.inf, -np.inf], np.nan)
