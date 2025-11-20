"""Stage 1 snapshot utilities.

Provides foundational helpers for deterministic data handling used by the
fixture extraction script and later snapshot processes.

Exports:
  NUMERIC_ABS_TOL: Absolute numeric comparison tolerance.
  NUMERIC_PRECISION: Decimal places for rounding.
  normalize_numeric(df): Round float columns to NUMERIC_PRECISION.
  sort_signals(df): Deterministically sort a signals DataFrame.
  sort_trades(df): Deterministically sort a trades DataFrame.
"""
from __future__ import annotations

from typing import Iterable
import pandas as pd

NUMERIC_ABS_TOLERANCE = 1e-6  # Retain legacy name
NUMERIC_ABS_TOL = NUMERIC_ABS_TOLERANCE  # Alias for newer imports
NUMERIC_PRECISION = 4


def normalize_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame with float columns rounded to NUMERIC_PRECISION.

    Operates in-place where feasible for performance, but returns df for chainability.
    """
    for col in df.select_dtypes(include=["float", "float64", "float32"]).columns:
        df[col] = df[col].round(NUMERIC_PRECISION)
    return df


def sort_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Deterministically sort signals by timestamp, ticker, signal_type if present."""
    keys = [c for c in ["timestamp", "ticker", "signal_type"] if c in df.columns]
    if not keys:
        return df.sort_index().reset_index(drop=True)
    return df.sort_values(keys).reset_index(drop=True)


def sort_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Deterministically sort trades by timestamp, ticker, order_id if present."""
    keys = [c for c in ["timestamp", "ticker", "order_id"] if c in df.columns]
    if not keys:
        return df.sort_index().reset_index(drop=True)
    return df.sort_values(keys).reset_index(drop=True)


__all__ = [
    "NUMERIC_ABS_TOLERANCE",
    "NUMERIC_ABS_TOL",
    "NUMERIC_PRECISION",
    "normalize_numeric",
    "sort_signals",
    "sort_trades",
]
