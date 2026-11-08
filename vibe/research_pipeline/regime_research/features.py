"""
Feature Engine — builds a date/timestamp-indexed feature table from OHLCV data.

Every feature is causal: a row's value depends only on data available at or
before that row's timestamp. Note the qualifier — this guarantee holds at the
granularity of the frame you pass in, and that distinction is the whole reason
this docstring is longer than it used to be.

Daily-derived features on intraday frames
-----------------------------------------
Several features (``adx_14``, ``slope_20d``, ``slope_50d``) are only meaningful
at daily granularity, so they resample to daily, compute, then reindex back
onto the caller's index with a forward fill. The daily bar for session D
summarizes *all* of session D, including its close. Forward-filling that value
onto session D's intraday bars would stamp the 16:00 close onto the 09:30 bar —
a look-ahead leak that was measured on real QQQ data, not hypothesized.

So those features apply a one-session shift **when and only when the input
frame is intraday**, via :func:`_reindex_causally`. On an intraday frame the
value at any bar of session D reflects data through session D-1's close.

Why the shift is conditional
----------------------------
:class:`DayRegimeLabeler` applies its own ``shift(1)`` and documents that
callers must not pre-shift. That contract is correct for a *daily* frame, where
one row is one session. Shifting here unconditionally would double-shift the
daily path and silently stale every regime label by an extra session. On an
intraday frame the labeler's ``shift(1)`` moves by a single bar — five minutes —
and protects nothing, which is why the shift has to live here instead.

The net contract:

- daily frame in   → unshifted; the labeler owns the shift (unchanged behavior)
- intraday frame in → shifted here; the labeler's shift is a near no-op

Features computed row-wise on the input frame (``atr_14``, ``atr_pctile``,
``realized_vol``, and the rolling percentiles) are causal on an intraday frame
already and are deliberately left alone; they still rely on the labeler's shift
when fed a daily frame.

Usage::

    engine = FeatureEngine()
    features = engine.compute(df, features=["atr_14", "atr_pctile", "gap_pct"])
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from vibe.common.indicators.batch import (
    adx_series,
    atr_series,
    linear_slope,
    rolling_percentile_rank,
    sma_series,
)

# Registry maps feature name → (compute_fn, dependencies)
# Dependencies are other feature names that must be computed first.
_FEATURE_REGISTRY: dict[str, list[str]] = {
    # Volatility
    "atr_14": [],
    "atr_pctile": ["atr_14"],
    "realized_vol": [],
    "vol_pctile": ["realized_vol"],
    "gap_pct": [],
    # Trend
    "dist_ma20_pct": [],
    "dist_ma50_pct": [],
    "slope_20d": [],
    "slope_50d": [],
    "adx_14": [],
    # Opening behavior
    "or_size_pct": [],
    "open_vol_pctile": [],
    "or_expansion": ["atr_14", "or_size_pct"],
    "gap_continuation": ["gap_pct"],
    # Market context
    "prev_day_range": [],
    "prev_day_trend_pct": [],
    "prev_close_location": [],
    "inside_day": [],
}

_ALL_FEATURES = list(_FEATURE_REGISTRY.keys())


class FeatureEngine:
    """Compute a table of regime-research features from an OHLCV DataFrame."""

    def compute(
        self,
        df: pd.DataFrame,
        features: list[str] | str = "all",
    ) -> pd.DataFrame:
        """
        Args:
            df: OHLCV DataFrame with columns open, high, low, close, volume.
                Index should be a DatetimeIndex (daily or intraday).
            features: List of feature names, or "all" for the full set.

        Returns:
            DataFrame with the same index as df and one column per feature.

        Raises:
            ValueError: If any name in features is not registered.
        """
        if features == "all":
            requested = list(_ALL_FEATURES)
        else:
            requested = list(features)

        unknown = set(requested) - set(_FEATURE_REGISTRY)
        if unknown:
            raise ValueError(f"Unknown features: {sorted(unknown)}. Available: {sorted(_FEATURE_REGISTRY)}")

        # Expand dependencies so we always have what's needed
        to_compute = _resolve_dependencies(requested)

        out = pd.DataFrame(index=df.index)

        for name in to_compute:
            out[name] = _compute_one(name, df, out)

        # Return only the requested features (not transitive deps that weren't asked for)
        return out[[c for c in requested if c in out.columns]]


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------

def _resolve_dependencies(requested: list[str]) -> list[str]:
    """Topological sort: ensure dependencies precede dependants."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        for dep in _FEATURE_REGISTRY.get(name, []):
            visit(dep)
        order.append(name)

    for name in requested:
        visit(name)

    return order


# ---------------------------------------------------------------------------
# Per-feature compute functions
# ---------------------------------------------------------------------------

def _compute_one(name: str, df: pd.DataFrame, ctx: pd.DataFrame) -> pd.Series:
    """Compute a single feature, pulling dependencies from ctx."""
    if name == "atr_14":
        return atr_series(df, 14)

    if name == "atr_pctile":
        return rolling_percentile_rank(ctx["atr_14"], 252).clip(0.0, 1.0)

    if name == "realized_vol":
        log_ret = np.log(df["close"] / df["close"].shift(1))
        return log_ret.rolling(window=20, min_periods=20).std()

    if name == "vol_pctile":
        return rolling_percentile_rank(ctx["realized_vol"], 252).clip(0.0, 1.0)

    if name == "gap_pct":
        prev_close = df["close"].shift(1)
        return (df["open"] - prev_close) / prev_close * 100

    if name == "dist_ma20_pct":
        sma20 = sma_series(df, 20)
        return (df["close"] - sma20) / sma20 * 100

    if name == "dist_ma50_pct":
        sma50 = sma_series(df, 50)
        return (df["close"] - sma50) / sma50 * 100

    if name == "slope_20d":
        daily_close = _to_daily_close(df)
        slope = linear_slope(daily_close, 20)
        return _reindex_causally(slope, df)

    if name == "slope_50d":
        daily_close = _to_daily_close(df)
        slope = linear_slope(daily_close, 50)
        return _reindex_causally(slope, df)

    if name == "adx_14":
        # Computed on daily bars, then mapped back to the caller's index. On an
        # intraday frame this must lag by a session; see _reindex_causally.
        daily_df = _to_daily_ohlcv(df)
        adx = adx_series(daily_df, 14)
        return _reindex_causally(adx, df)

    if name == "or_size_pct":
        # Requires OR high/low columns; return NaN if not present
        if "or_high" in df.columns and "or_low" in df.columns:
            prev_close = df["close"].shift(1)
            return (df["or_high"] - df["or_low"]) / prev_close.replace(0, np.nan) * 100
        return pd.Series(np.nan, index=df.index)

    if name == "open_vol_pctile":
        first_bar_vol = df["volume"].copy()
        return rolling_percentile_rank(first_bar_vol, 252).clip(0.0, 1.0)

    if name == "or_expansion":
        if "or_size_pct" in ctx.columns and "atr_14" in ctx.columns:
            prev_atr = ctx["atr_14"].shift(1)
            return ctx["or_size_pct"] / prev_atr.replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)

    if name == "gap_continuation":
        if "gap_pct" in ctx.columns:
            prev_trend = (df["close"] - df["open"]).shift(1)
            return (np.sign(ctx["gap_pct"]) == np.sign(prev_trend)).astype(float) * 2 - 1
        return pd.Series(np.nan, index=df.index)

    if name == "prev_day_range":
        daily_df = _to_daily_ohlcv(df)
        day_range = (daily_df["high"] - daily_df["low"]).shift(1)
        return day_range.reindex(df.index, method="ffill")

    if name == "prev_day_trend_pct":
        daily_df = _to_daily_ohlcv(df)
        trend = ((daily_df["close"] - daily_df["open"]) / daily_df["open"].replace(0, np.nan) * 100).shift(1)
        return trend.reindex(df.index, method="ffill")

    if name == "prev_close_location":
        daily_df = _to_daily_ohlcv(df)
        hl_range = (daily_df["high"] - daily_df["low"]).replace(0, np.nan)
        loc = ((daily_df["close"] - daily_df["low"]) / hl_range).shift(1)
        return loc.reindex(df.index, method="ffill")

    if name == "inside_day":
        daily_df = _to_daily_ohlcv(df)
        prev_high = daily_df["high"].shift(1)
        prev_low = daily_df["low"].shift(1)
        inside = ((daily_df["high"] < prev_high) & (daily_df["low"] > prev_low)).astype(float)
        return inside.reindex(df.index, method="ffill")

    raise ValueError(f"No compute function for feature: {name!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reindex_causally(daily_values: pd.Series, df: pd.DataFrame) -> pd.Series:
    """Map a daily-frequency series onto ``df.index`` without look-ahead.

    A daily bar for session D summarizes the whole of session D. Forward-filling
    it onto session D's intraday bars would let a 09:30 bar see the 16:00 close,
    so on an intraday frame the series is lagged one session first: bars of
    session D carry session D-1's value.

    On a daily frame no shift is applied. One row is already one session there,
    and :class:`DayRegimeLabeler` applies its own ``shift(1)``; shifting here too
    would lag every label by an extra session. See the module docstring.
    """
    if _is_intraday(df):
        daily_values = daily_values.shift(1)
    return daily_values.reindex(df.index, method="ffill")


def _to_daily_close(df: pd.DataFrame) -> pd.Series:
    """Return daily close series; if df is already daily-ish, return close as-is."""
    if _is_intraday(df):
        return df["close"].resample("D").last().dropna()
    return df["close"]


def _to_daily_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return daily OHLCV; resample if intraday."""
    if _is_intraday(df):
        return df.resample("D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
    return df


def _is_intraday(df: pd.DataFrame) -> bool:
    """Heuristic: if median time delta < 1 day, data is intraday."""
    if len(df) < 2:
        return False
    delta = pd.Series(df.index).diff().dropna().median()
    return delta < pd.Timedelta("1D")
