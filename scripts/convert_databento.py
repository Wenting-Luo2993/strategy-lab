#!/usr/bin/env python3
"""
One-time conversion: Databento CSV.zst -> Parquet

Paths are configured via .env (or environment variables):
    BACKTEST__DATABENTO_DIR   raw source files   (default: ./data/databento)
    BACKTEST__DATA_DIR        output Parquet dir  (default: ./data/parquet)

Run once before first backtest; re-run only if source files change.

Usage:
    python scripts/convert_databento.py              # convert all symbols
    python scripts/convert_databento.py --symbol QQQ # one symbol only
    python scripts/convert_databento.py --dry-run    # validate only, no write
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import zstandard

# Load .env if present (optional dependency — skip silently if not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _resolve(env_key: str, default: str) -> Path:
    raw = os.environ.get(env_key, default)
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p

DATABENTO_DIR = _resolve("BACKTEST__DATABENTO_DIR", "data/databento")
PARQUET_DIR   = _resolve("BACKTEST__DATA_DIR",       "data/parquet")
MARKET_TZ = "America/New_York"
MARKET_OPEN = "09:30"
MARKET_CLOSE = "15:59"  # inclusive upper bound for between_time


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_csv_zst(path: Path) -> pd.DataFrame:
    with open(path, "rb") as fh:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(fh) as reader:
            df = pd.read_csv(
                reader,
                usecols=["ts_event", "open", "high", "low", "close", "volume"],
                dtype={
                    "open": "float64", "high": "float64",
                    "low": "float64", "close": "float64",
                    "volume": "int64",
                },
            )
    df["ts_event"] = (
        pd.to_datetime(df["ts_event"], utc=True)
        .dt.tz_convert(MARKET_TZ)
    )
    df = df.set_index("ts_event").sort_index()
    df = df.between_time(MARKET_OPEN, MARKET_CLOSE)
    return df


# ── Split detection & adjustment ───────────────────────────────────────────────

def _detect_splits(df: pd.DataFrame) -> List[Tuple[pd.Timestamp, int]]:
    """
    Detect forward stock splits from overnight price gaps in 1-minute data.
    Returns (split_date, integer_ratio) pairs in chronological order.

    A split is identified when:
      - prev_day_close / curr_day_open >= 1.5  (price dropped significantly overnight)
      - that ratio rounds to a clean integer >= 2
      - the raw ratio is within 5% of the integer (rules out large but non-split gaps)
    """
    daily_close = df["close"].resample("D").last().dropna()
    daily_open  = df["open"].resample("D").first().dropna()
    common = daily_close.index.intersection(daily_open.index)
    if len(common) < 2:
        return []

    prev_close = daily_close.loc[common].shift(1).dropna()
    curr_open  = daily_open.loc[prev_close.index]

    splits = []
    for date in prev_close.index:
        pc, co = prev_close[date], curr_open[date]
        if pc <= 0 or co <= 0:
            continue
        raw_ratio = pc / co
        if raw_ratio < 1.5:
            continue
        ratio = round(raw_ratio)
        if ratio < 2:
            continue
        if abs(raw_ratio - ratio) / ratio > 0.05:
            continue
        splits.append((date, ratio))

    return splits


def _apply_splits(df: pd.DataFrame, splits: List[Tuple[pd.Timestamp, int]]) -> pd.DataFrame:
    """
    Backward-adjust prices for all detected splits so the full series is on
    the current (post-all-splits) price scale.

    Processed reverse-chronologically so multiple splits compound correctly:
      e.g. TSLA had 5:1 (2020) then 3:1 (2022) → pre-2020 data divided by 15 total.
    """
    df = df.copy()
    price_cols = ["open", "high", "low", "close"]
    for split_date, ratio in sorted(splits, reverse=True):
        mask = df.index.normalize() < split_date
        df.loc[mask, price_cols] = df.loc[mask, price_cols] / ratio
        df.loc[mask, "volume"]   = (df.loc[mask, "volume"] * ratio).astype("int64")
    return df


def _symbol_from_path(path: Path) -> str:
    # e.g. xnas-itch-20180501-20260428.ohlcv-1m.QQQ.csv.zst → QQQ
    parts = path.name.split(".")
    return parts[-3]


# ── Validation ─────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    symbol: str
    total_bars: int
    date_range: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _validate(df: pd.DataFrame, symbol: str) -> ValidationResult:
    result = ValidationResult(
        symbol=symbol,
        total_bars=len(df),
        date_range=(
            f"{df.index[0].date()} to {df.index[-1].date()}"
            if len(df) else "empty"
        ),
    )

    if len(df) == 0:
        result.errors.append("No bars after market-hours filter")
        return result

    # ── Hard errors (block Parquet write) ─────────────────────────────────────

    bad_high = (df["high"] < df[["open", "close"]].max(axis=1)).sum()
    if bad_high:
        result.errors.append(f"high < max(open,close) in {bad_high} bars")

    bad_low = (df["low"] > df[["open", "close"]].min(axis=1)).sum()
    if bad_low:
        result.errors.append(f"low > min(open,close) in {bad_low} bars")

    hl_inverted = (df["high"] < df["low"]).sum()
    if hl_inverted:
        result.errors.append(f"high < low in {hl_inverted} bars")

    non_positive = (df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()
    if non_positive:
        result.errors.append(f"Non-positive price in {non_positive} bars")

    # ── Warnings (logged but do not block write) ───────────────────────────────

    zero_vol = (df["volume"] == 0).sum()
    if zero_vol:
        result.warnings.append(f"Zero-volume bars: {zero_vol}")

    # Intrabar spread > 10% of close — possible bad ticks
    spread_pct = (df["high"] - df["low"]) / df["close"]
    outliers = (spread_pct > 0.10).sum()
    if outliers:
        result.warnings.append(
            f"Intrabar spread >10% in {outliers} bars — possible bad ticks"
        )

    # Overnight gap > 20% — possible unadjusted split
    daily = df["close"].resample("D").last().dropna()
    daily_open = df["open"].resample("D").first().dropna()
    # Align on shared dates
    common = daily.index.intersection(daily_open.index)
    if len(common) > 1:
        prev_close = daily.loc[common].shift(1).dropna()
        curr_open = daily_open.loc[prev_close.index]
        gap = ((curr_open - prev_close) / prev_close).abs()
        large_gaps = gap[gap > 0.20]
        if not large_gaps.empty:
            dates = [str(d.date()) for d in large_gaps.index[:3]]
            suffix = " ..." if len(large_gaps) > 3 else ""
            result.warnings.append(
                f"Overnight gap >20% on {len(large_gaps)} day(s)"
                f" (possible unadjusted split): {', '.join(dates)}{suffix}"
            )

    # Trading day coverage check (expect ≥ 90% of ~252 days/year)
    trading_days = df.index.normalize().nunique()
    years = (df.index[-1] - df.index[0]).days / 365.25
    expected = max(1, int(years * 252))
    coverage = trading_days / expected
    if coverage < 0.90:
        result.warnings.append(
            f"Trading day coverage: {trading_days}/{expected} ({coverage:.1%})"
            " — data may have gaps"
        )

    return result


# ── Per-symbol conversion ──────────────────────────────────────────────────────

def _convert_one(path: Path, out_dir: Path, dry_run: bool) -> ValidationResult:
    symbol = _symbol_from_path(path)

    print(f"  {symbol:6s}  loading {path.name} ...", end=" ", flush=True)
    df = _load_csv_zst(path)
    print(f"{len(df):>9,} bars", end="  ", flush=True)

    # Detect and apply split adjustments before validation
    splits = _detect_splits(df)
    if splits:
        df = _apply_splits(df, splits)

    result = _validate(df, symbol)

    status = "OK" if result.ok else "FAIL"
    warn_tag = f"  {len(result.warnings)} warning(s)" if result.warnings else ""
    print(f"[{status}]  {result.date_range}{warn_tag}")

    for date, ratio in splits:
        n_adjusted = (df.index.normalize() < date).sum()
        print(f"           SPLIT {date.date()} {ratio}:1  ({n_adjusted:,} bars adjusted)")

    for msg in result.errors:
        print(f"           ERROR: {msg}")
    for msg in result.warnings:
        print(f"           WARN : {msg}")

    if result.ok and not dry_run:
        out_path = out_dir / f"{symbol}.parquet"
        df.to_parquet(out_path, engine="pyarrow", compression="snappy", index=True)
        size_mb = out_path.stat().st_size / 1_000_000
        print(f"           >> {out_path.name}  ({size_mb:.1f} MB)")

    return result


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Databento CSV.zst files to Parquet"
    )
    parser.add_argument("--symbol", help="Convert a single symbol only (e.g. QQQ)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and report only — do not write Parquet files",
    )
    args = parser.parse_args()

    if not DATABENTO_DIR.exists():
        print(f"ERROR: {DATABENTO_DIR} not found", file=sys.stderr)
        sys.exit(1)

    files = sorted(DATABENTO_DIR.glob("*.csv.zst"))
    if args.symbol:
        files = [f for f in files if f".{args.symbol}." in f.name]
        if not files:
            print(f"ERROR: no .csv.zst file found for symbol '{args.symbol}'", file=sys.stderr)
            sys.exit(1)

    if not files:
        print(f"No .csv.zst files found in {DATABENTO_DIR}", file=sys.stderr)
        sys.exit(1)

    PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    label = "  [dry-run - no files written]" if args.dry_run else ""
    print(f"Converting {len(files)} file(s){label}\n")

    results = [_convert_one(f, PARQUET_DIR, args.dry_run) for f in files]

    # ── Summary ────────────────────────────────────────────────────────────────
    n_ok = sum(1 for r in results if r.ok)
    n_warn = sum(1 for r in results if r.warnings)
    print(f"\n{'-' * 56}")
    print(f"  {n_ok}/{len(results)} symbols clean"
          + (f"  ({n_warn} with warnings)" if n_warn else ""))
    if not args.dry_run and n_ok > 0:
        print(f"  Parquet files: {PARQUET_DIR}")

    if n_ok < len(results):
        print("  Symbols with errors must be fixed before backtesting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
