"""
Feature Engine — builds a date/timestamp-indexed feature table from OHLCV data.

All features are forward-observable: each row's value depends only on data up to
and including that row's timestamp.  Callers must not shift the output; the
DayRegimeLabeler handles its own shift(1) before assigning labels.

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
        return slope.reindex(df.index, method="ffill")

    if name == "slope_50d":
        daily_close = _to_daily_close(df)
        slope = linear_slope(daily_close, 50)
        return slope.reindex(df.index, method="ffill")

    if name == "adx_14":
        # If intraday, resample to daily then forward-fill; if already daily, use as-is
        daily_df = _to_daily_ohlcv(df)
        adx = adx_series(daily_df, 14)
        return adx.reindex(df.index, method="ffill")

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
