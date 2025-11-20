"""Fixture data extraction script.

Generates a deterministic subset of cached market data for testing (snapshot
framework Stage 1). Reads per-ticker Parquet files from the workspace
`data_cache/` directory and writes a fixture directory under
`tests/scenarios/<fixture_name>` containing one Parquet (or CSV) per ticker plus
`metadata.json`.

Determinism Guarantees:
  * Rows sorted by timestamp ascending.
  * All original columns preserved.
  * Numeric columns normalized (rounded) to fixed precision to prevent float
    drift in downstream diffs.
  * Metadata includes row counts per ticker and a content hash for integrity.

Usage (CLI):
  python scripts/extract_fixture_data.py --tickers AAPL NVDA \
      --start-date 2025-11-07 --end-date 2025-11-07 --fixture-name orb_smoke

Date Filtering:
  If source Parquet contains a 'timestamp' column parseable as datetime, rows
  are filtered to the inclusive [start_date, end_date] date range (UTC date
  based on timestamp.date()). If date columns aren't present, all rows are kept.

Metadata Schema (version 1):
  {
    "version": 1,
    "fixture_name": str,
    "tickers": [str,...],
    "start_date": str (YYYY-MM-DD),
    "end_date": str (YYYY-MM-DD),
    "created_at": ISO8601 UTC,
    "generator_version": "1.0.0",
    "git_commit": str or "UNKNOWN",
    "rows_per_ticker": {ticker: int},
    "content_hash": str (SHA256 over concatenated per-ticker hashes),
    "numeric_precision": 6
  }

NOTE: This script is intentionally lightweight; advanced filtering (e.g.
session hours) can be added later without breaking metadata contract if the
version is bumped.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple

import pandas as pd

# Ensure 'python' project root is on sys.path before attempting local imports
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from src.utils.workspace import resolve_workspace_path
    from python.src.utils.snapshot_utils import normalize_numeric, NUMERIC_PRECISION
except ImportError:  # Fallback if relative import context differs
    # Secondary attempt if executed from a different entry context
    from python.src.utils.workspace import resolve_workspace_path  # type: ignore
    from python.src.utils.snapshot_utils import normalize_numeric, NUMERIC_PRECISION  # type: ignore
GENERATOR_VERSION = "1.0.0"


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Source data file not found: {path}")
    return pd.read_parquet(path)


def _filter_date_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """Filter DataFrame to inclusive date range.

    Supports either a 'timestamp' column or an index named 'timestamp'. If
    neither is present, returns the DataFrame unchanged.
    """
    start_d = pd.to_datetime(start_date).date()
    end_d = pd.to_datetime(end_date).date()
    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], errors="coerce")
        mask = (ts.dt.date >= start_d) & (ts.dt.date <= end_d)
        return df.loc[mask].copy()
    elif df.index.name == "timestamp" or isinstance(df.index, pd.DatetimeIndex):
        ts = pd.to_datetime(df.index, errors="coerce")
        mask = (ts.date >= start_d) & (ts.date <= end_d)
        return df.loc[mask].copy()
    return df


# Numeric normalization delegated to shared utility for cross-stage consistency.


def _sort_and_index(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure deterministic ordering and timestamp index retention.

    If a 'timestamp' column exists, sort by it and set it as the index.
    If index already named 'timestamp', just sort by the index.
    Otherwise, fallback to index sort.
    """
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp")
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.set_index("timestamp")
    elif df.index.name == "timestamp":
        # Ensure sorted by existing timestamp index
        df = df.sort_index()
    else:
        df = df.sort_index()
    if df.index.name != "timestamp" and "timestamp" not in df.columns:
        # Leave as-is; no timestamp information available
        return df
    # Guarantee index name
    df.index.name = "timestamp"
    return df


def _hash_df(df: pd.DataFrame) -> str:
    """Produce a stable hash including the timestamp index.

    Resets index into a column for hashing to avoid differences in index
    serialization formats while still incorporating timestamp values.
    """
    csv_bytes = df.reset_index().to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()


def _get_git_commit() -> str:
    try:
        import subprocess
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
        return commit
    except Exception:
        return "UNKNOWN"


def extract_fixture(
    tickers: List[str],
    start_date: str,
    end_date: str,
    source_path: Path,
    dest_path: Path,
    fixture_name: str,
) -> Dict[str, int]:
    dest_path.mkdir(parents=True, exist_ok=True)
    rows_per_ticker: Dict[str, int] = {}
    per_ticker_hashes: List[str] = []

    for t in tickers:
        file_path = source_path / f"{t}_5m.parquet"
        df = _read_parquet(file_path)
        df = _filter_date_range(df, start_date, end_date)
        df = _sort_and_index(df)
        df = normalize_numeric(df)
        rows_per_ticker[t] = len(df)
        # Write out as parquet (preserve types)
        out_file = dest_path / f"{t}.parquet"
        # Preserve index (timestamp) in parquet output for downstream tests
        df.to_parquet(out_file, index=True)
        per_ticker_hashes.append(_hash_df(df))

    content_hash = hashlib.sha256("".join(per_ticker_hashes).encode("utf-8")).hexdigest()
    metadata = {
        "version": 1,
        "fixture_name": fixture_name,
        "tickers": tickers,
        "start_date": start_date,
        "end_date": end_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generator_version": GENERATOR_VERSION,
        "git_commit": _get_git_commit(),
        "rows_per_ticker": rows_per_ticker,
        "content_hash": content_hash,
        "numeric_precision": NUMERIC_PRECISION,
        "source_path": str(source_path),
    }
    with open(dest_path / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
    return rows_per_ticker


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract deterministic fixture data for snapshot tests")
    parser.add_argument("--tickers", nargs="+", required=True, help="List of tickers to include")
    parser.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=False, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--source-path", default="data_cache", help="Relative or absolute path to source data cache")
    parser.add_argument(
        "--dest-path",
        default="tests/scenarios",
        help="Base destination directory for fixture (fixture subdir will be created)",
    )
    parser.add_argument("--fixture-name", required=False, help="Optional explicit fixture name")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    start_date = args.start_date
    end_date = args.end_date or start_date
    tickers = args.tickers
    fixture_name = args.fixture_name or f"{'_'.join(tickers)}_{start_date}_{end_date}"

    source_path = resolve_workspace_path(args.source_path)
    base_dest = resolve_workspace_path(args.dest_path)
    dest_path = base_dest / fixture_name

    rows = extract_fixture(tickers, start_date, end_date, source_path, dest_path, fixture_name)
    print(f"Fixture '{fixture_name}' created at {dest_path}. Rows per ticker: {rows}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
