"""Stage 1 fixture generation tests.

Validates determinism of the extract_fixture_data script utilities.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pandas as pd

from src.utils.workspace import resolve_workspace_path
from scripts.extract_fixture_data import extract_fixture
from src.utils.snapshot_stage1 import NUMERIC_PRECISION as SNAP_PRECISION


def _pick_tickers_and_dates(source_path: Path, limit: int = 2):
    parquet_files = sorted(source_path.glob("*_5m.parquet"))
    assert parquet_files, "No source parquet files found in data_cache for test"
    tickers = [p.name.split("_5m.parquet")[0] for p in parquet_files[:limit]]
    # Derive date from first file
    df = pd.read_parquet(parquet_files[0])
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], errors="coerce")
        assert not ts.isna().all(), "Timestamp column unreadable for date derivation"
        date = ts.dt.date.iloc[0].isoformat()
    elif isinstance(df.index, pd.DatetimeIndex):
        ts = pd.to_datetime(df.index, errors="coerce")
        assert not ts.isna().all(), "Timestamp index unreadable for date derivation"
        date = ts[0].date().isoformat()
    else:
        # Fallback fixed date if missing timestamp
        date = "2025-01-01"
    return tickers, date


def test_fixture_generation_deterministic(tmp_path: Path):
    source_path = resolve_workspace_path("data_cache")
    tickers, date = _pick_tickers_and_dates(source_path)

    dest_base = tmp_path / "scenarios"
    first_dest = dest_base / "run1"
    second_dest = dest_base / "run2"

    rows1 = extract_fixture(tickers, date, date, source_path, first_dest, "run1")
    rows2 = extract_fixture(tickers, date, date, source_path, second_dest, "run2")

    # Metadata comparison
    meta1 = json.loads((first_dest / "metadata.json").read_text())
    meta2 = json.loads((second_dest / "metadata.json").read_text())

    assert meta1["rows_per_ticker"] == meta2["rows_per_ticker"]
    assert meta1["content_hash"] == meta2["content_hash"], "Content hash must be stable across identical runs"
    assert meta1["numeric_precision"] == SNAP_PRECISION

    # Per-ticker parquet equality
    for t in tickers:
        df1 = pd.read_parquet(first_dest / f"{t}.parquet")
        df2 = pd.read_parquet(second_dest / f"{t}.parquet")
        # Index expectations
        assert isinstance(df1.index, pd.DatetimeIndex), "Fixture must have datetime index (timestamp)"
        assert isinstance(df2.index, pd.DatetimeIndex), "Fixture must have datetime index (timestamp)"
        assert df1.index.is_monotonic_increasing, "Timestamp index must be sorted"
        assert df2.index.is_monotonic_increasing, "Timestamp index must be sorted"
        assert df1.index.equals(df2.index), "Timestamp indices differ between runs"
        # Ensure identical shape and values
        assert df1.equals(df2), f"Data mismatch for ticker {t}"  # pandas.equals handles NaN equality

    # Clean up (tmp_path auto-removed, but explicit for clarity)
    shutil.rmtree(dest_base, ignore_errors=True)
