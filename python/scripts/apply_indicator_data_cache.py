"""Bulk indicator precomputation for cached market data.

This script performs a one-time enrichment of every parquet file under a
`data_cache` directory (default) by adding the following indicators if they
are missing:

  * ORB (Opening Range Breakout) levels & breakout flags
  * EMA 20, EMA 30, EMA 50, EMA 200
  * RSI 14
  * ATR 14

It updates each parquet file IN PLACE (atomic temp -> rename) so downstream
backtests, replays and live simulations can avoid recomputing indicators
bar-by-bar, dramatically reducing per-cycle latency.

Usage (PowerShell from project root after venv activation):
	python scripts/apply_indicator_data_cache.py \
		--cache-dir data_cache \
		--timeframes 5m 1m \
		--orb-start 09:30 \
		--orb-window 5 \
		--body-pct 0.5 \
		--force

Key options:
  --dry-run    : Do everything except write modified parquet back.
  --force      : Recalculate indicators even if columns exist.
  --timeframes : Limit processing to specified timeframes (default all detected).
  --workers    : Optional parallelism (process pool); default 0 (sequential).

Indicator Column Names (pandas_ta conventions):
  EMA_* columns: EMA_20, EMA_30, EMA_50, EMA_200
  RSI_* column: RSI_14
  ATR column: ATRr_14 or ATR_14 (pandas_ta can vary; we preserve whatever it returns)
  ORB columns: ORB_High, ORB_Low, ORB_Range, ORB_Breakout

The script is idempotent unless --force is supplied.

Example invocations:
  # Dry run preview
  python scripts/apply_indicator_data_cache.py --cache-dir data_cache --timeframes 5m --dry-run

  # Full run, sequential
  python scripts/apply_indicator_data_cache.py --cache-dir data_cache --timeframes 5m

  # Force recompute and parallel with 4 workers
  python scripts/apply_indicator_data_cache.py --cache-dir data_cache --timeframes 5m --force --workers 4

	# Ticker filter + enrichment export + downcast (prices float32, volume int32)
	# (Escaped backslashes for Windows paths to avoid Python invalid escape warnings in docstring)
	python scripts/apply_indicator_data_cache.py --cache-dir data_cache --timeframes 5m --tickers AAPL --export-enriched-dir data_cache\\enriched_after_run --downcast

	# Actual command:
	python scripts/apply_indicator_data_cache.py --cache-dir data_cache --timeframes 5m --workers 4 --downcast --report-file indicator_report.csv
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import List, Tuple, Dict, Set
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Dynamic path setup so the script can be run from either project root OR the
# python/ subdirectory without failing to locate the sibling top-level `src`.
# ---------------------------------------------------------------------------
THIS_FILE = Path(__file__).resolve()
# project root: strategy-lab/ (parent of python/)
PROJECT_ROOT = THIS_FILE.parents[2] if len(THIS_FILE.parents) >= 2 else THIS_FILE.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))
# also ensure python/ (parent of scripts/) is on path to allow local relative imports
PYTHON_DIR = THIS_FILE.parents[1]
if str(PYTHON_DIR) not in sys.path:
	sys.path.insert(0, str(PYTHON_DIR))

# Import project indicator factory & registrations (after path injection)
try:
	from src.indicators import IndicatorFactory  # ensures registry populated
	from src.indicators.orb import calculate_orb_levels  # noqa: F401 (registration side-effect)
	from src.utils.logger import get_logger
except ModuleNotFoundError as e:
	print("[FATAL] Unable to import project modules. Ensure you run from project root or that PYTHONPATH includes project root.")
	print(f"[DETAIL] {e}")
	sys.exit(1)

logger = get_logger("CacheIndicatorPrecompute")

EMA_LENGTHS = [20, 30, 50, 200]
RSI_LENGTH = 14
ATR_LENGTH = 14
ORB_DEFAULT_START = "09:30"
ORB_DEFAULT_WINDOW = 5
ORB_DEFAULT_BODY_PCT = 0.5

TIMEFRAME_WHITELIST = {"1m", "5m", "15m", "1h", "1d"}


def parse_args() -> argparse.Namespace:
	p = argparse.ArgumentParser(description="Bulk precompute indicators for cached parquet files")
	p.add_argument("--cache-dir", default="data_cache", help="Root cache directory containing parquet files")
	p.add_argument("--tickers", nargs="*", help="Optional list of ticker symbols to process (default: all discovered)")
	p.add_argument("--timeframes", nargs="*", help="Optional list of timeframe filters (e.g. 5m 1m)")
	p.add_argument("--orb-start", default=ORB_DEFAULT_START, help="Opening range start time HH:MM")
	p.add_argument("--orb-window", type=int, default=ORB_DEFAULT_WINDOW, help="Opening range duration minutes")
	p.add_argument("--body-pct", type=float, default=ORB_DEFAULT_BODY_PCT, help="ORB breakout body percentage threshold")
	p.add_argument("--force", action="store_true", help="Recalculate indicators even if columns already present")
	p.add_argument("--dry-run", action="store_true", help="Do not write changes back to parquet files")
	p.add_argument("--workers", type=int, default=0, help="Process pool worker count (0 = sequential)")
	p.add_argument("--limit", type=int, help="Process only first N files (debug/perf testing)")
	p.add_argument("--report-file", type=str, help="Optional path to write a CSV summary (dry-run or full)")
	p.add_argument("--export-enriched-dir", type=str, help="Optional directory to write enriched per-file CSV snapshots (includes OHLCV + indicators)")
	p.add_argument("--downcast", action="store_true", help="Enable numeric downcast (prices/indicators -> float32 rounded 4dp; volume -> int32 safe cast)")
	return p.parse_args()


def discover_parquet_files(cache_dir: Path) -> List[Path]:
	return sorted(cache_dir.glob("*.parquet"))


def extract_symbol_timeframe(path: Path) -> Tuple[str, str]:
	# Expected rolling filename: SYMBOL_timeframe.parquet
	# Legacy may include dates: SYMBOL_timeframe_2025-08-01_2025-09-29.parquet
	stem = path.stem  # without .parquet
	parts = stem.split("_")
	if len(parts) < 2:
		return stem, "unknown"
	symbol = parts[0]
	timeframe = parts[1]
	return symbol, timeframe


def needs_indicator_columns(df: pd.DataFrame, force: bool, orb_params: Dict[str, any]) -> Dict[str, bool]:
	"""Return mapping of indicator group -> bool (needs generation)."""
	status = {}
	# ORB: check ORB_Breakout presence
	status["orb"] = force or ("ORB_Breakout" not in df.columns)
	# EMA: need all required lengths present
	for length in EMA_LENGTHS:
		col_match = any(c.startswith(f"EMA_{length}") for c in df.columns)
		if force or not col_match:
			status[f"ema_{length}"] = True
	# RSI (search RSI_14 or RSI_14 suffix pattern)
	rsi_present = any(c.startswith(f"RSI_{RSI_LENGTH}") for c in df.columns)
	status["rsi"] = force or not rsi_present
	# ATR (pandas_ta names ATRr_14 or ATR_14 depending version)
	atr_present = any(c.startswith("ATR") and str(ATR_LENGTH) in c for c in df.columns)
	status["atr"] = force or not atr_present
	return status


def apply_indicators(df: pd.DataFrame, orb_start: str, orb_window: int, body_pct: float, force: bool) -> pd.DataFrame:
	# Normalize column names to lowercase open/high/low/close/volume if possible for consistency
	rename_map = {}
	for col in df.columns:
		lc = col.lower()
		if lc in {"open", "high", "low", "close", "volume"} and col != lc:
			rename_map[col] = lc
	if rename_map:
		df = df.rename(columns=rename_map)

	if not isinstance(df.index, pd.DatetimeIndex):
		# Attempt to promote a column named 'datetime' or 'timestamp'
		for c in ["datetime", "timestamp"]:
			if c in df.columns:
				df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
				df = df.set_index(c)
				break
	if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
		df.index = df.index.tz_localize("UTC")

	indicator_plan = []
	# Determine which indicators missing
	missing_map = needs_indicator_columns(df, force, {"start_time": orb_start, "duration_minutes": orb_window, "body_pct": body_pct})
	if missing_map.get("orb"):
		indicator_plan.append({"name": "orb_levels", "params": {"start_time": orb_start, "duration_minutes": orb_window, "body_pct": body_pct}})
	for length in EMA_LENGTHS:
		if missing_map.get(f"ema_{length}"):
			indicator_plan.append({"name": "ema", "params": {"length": length}})
	if missing_map.get("rsi"):
		indicator_plan.append({"name": "rsi", "params": {"length": RSI_LENGTH}})
	if missing_map.get("atr"):
		indicator_plan.append({"name": "atr", "params": {"length": ATR_LENGTH}})

	if not indicator_plan:
		logger.info("All requested indicators already present; skipping generation for this file.")
		return df

	logger.info(f"Applying {len(indicator_plan)} indicator groups: {[p['name'] for p in indicator_plan]}")
	enriched = IndicatorFactory.apply(df, indicator_plan)
	# Attach metadata of what was applied for downstream dry-run reporting
	enriched.attrs["applied_indicators"] = [p['name'] for p in indicator_plan]
	return enriched



def downcast_numeric(df: pd.DataFrame, exclude: Set[str] | None = None, decimals: int = 4) -> Tuple[pd.DataFrame, List[str]]:
	"""Downsize numeric columns to reduce storage footprint.

	Behavior:
	- Price/indicator numeric columns (non-volume) are rounded to ``decimals`` and cast to float32.
	- ``volume`` column (case-insensitive) is converted to the smallest safe integer dtype (preferring int32, falling back to int64).

	Parameters
	----------
	df : DataFrame
		Input (already enriched) frame.
	exclude : set[str] | None
		Optional additional column names to exclude from processing (case-insensitive).
	decimals : int
		Decimal places for rounding non-volume numeric columns.

	Returns
	-------
	(df, processed_cols) : Tuple[DataFrame, List[str]]
		Modified DataFrame and list of columns downcasted (including 'volume' if converted).

	Notes
	-----
	"float36" does not exist; float32 chosen for adequate precision of prices & indicators.
	Volume integers typically fit in int32 for intraday bars; if max exceeds int32, we use int64.
	"""
	if exclude is None:
		exclude = set()
	exclude_normalized = {c.lower() for c in exclude}
	processed: List[str] = []
	volume_col = None
	for col in df.columns:
		if col.lower() == "volume":
			volume_col = col
			break
	for col in df.columns:
		if col.lower() in exclude_normalized:
			continue
		# Skip volume here; handle separately
		if volume_col and col == volume_col:
			continue
		series = df[col]
		if pd.api.types.is_numeric_dtype(series):
			try:
				# Round then cast to float32
				df[col] = series.round(decimals).astype("float32")
				processed.append(col)
			except Exception as e:  # pragma: no cover - defensive logging
				logger.warning(f"Downcast failed for column {col}: {e}")
	# Handle volume conversion last
	if volume_col and volume_col.lower() not in exclude_normalized:
		vol_series = df[volume_col]
		if pd.api.types.is_numeric_dtype(vol_series):
			try:
				# If float but integral values, cast to int
				if pd.api.types.is_float_dtype(vol_series):
					# Check if all non-null values are near integer
					all_int_like = (vol_series.dropna() == vol_series.dropna().round()).all()
					if all_int_like:
						vol_series = vol_series.round().astype("int64")
				else:
					# Leave as is (fallback to rounding and float32)
					vol_series = vol_series.round().astype("float32")
				# Attempt int32 downcast when possible
				if pd.api.types.is_integer_dtype(vol_series):
					max_val = vol_series.max()
					if max_val <= 2_147_483_647:
						vol_series = vol_series.astype("int32")
				df[volume_col] = vol_series
				processed.append(volume_col)
			except Exception as e:
				logger.warning(f"Volume int downcast failed for column {volume_col}: {e}")
	if processed:
		logger.info(f"Downcasted {len(processed)} columns (float32 prices/indicators, int volume where possible): {processed}")
	df.attrs["downcast_columns"] = processed
	return df, processed


def process_file(path: Path, symbol_filters: Set[str], timeframe_filters: Set[str], orb_start: str, orb_window: int, body_pct: float, force: bool, dry_run: bool, export_dir: Path | None, do_downcast: bool) -> Tuple[Path, bool, str, List[str], List[str]]:
	symbol, timeframe = extract_symbol_timeframe(path)
	if symbol_filters and symbol not in symbol_filters:
		return path, False, f"Skipped (symbol {symbol} not in filter)", [], []
	if timeframe_filters and timeframe not in timeframe_filters:
		return path, False, f"Skipped (timeframe {timeframe} not in filter)", [], []
	if timeframe not in TIMEFRAME_WHITELIST:
		return path, False, f"Skipped (timeframe {timeframe} not whitelisted)", [], []
	try:
		df = pd.read_parquet(path)
	except Exception as e:
		return path, False, f"Read failed: {e}", [], []
	try:
		logger.info(f"Processing {path.name} symbol={symbol} timeframe={timeframe} rows={len(df)}")
		enriched = apply_indicators(df, orb_start, orb_window, body_pct, force)
		downcasted: List[str] = []
		if do_downcast:
			# Downcast numeric columns (excluding volume) after indicator enrichment
			enriched, downcasted = downcast_numeric(enriched)
		applied = enriched.attrs.get("applied_indicators", [])
		if dry_run:
			# In dry-run we still can export enriched CSV if requested
			if export_dir is not None:
				try:
					export_dir.mkdir(parents=True, exist_ok=True)
					enriched.to_csv(export_dir / f"{path.stem}_enriched.csv")
					logger.info(f"Dry-run export written: {export_dir / (path.stem + '_enriched.csv')}")
				except Exception as ee:
					logger.warning(f"Failed dry-run export for {path.name}: {ee}")
			return path, True, f"Dry-run (would apply: {applied}; downcast: {downcasted})", applied, downcasted
		# Atomic write
		tmp_path = path.with_suffix(path.suffix + ".tmp")
		enriched.to_parquet(tmp_path)
		tmp_path.replace(path)
		if export_dir is not None:
			try:
				export_dir.mkdir(parents=True, exist_ok=True)
				enriched.to_csv(export_dir / f"{path.stem}_enriched.csv")
				logger.info(f"Export written: {export_dir / (path.stem + '_enriched.csv')}")
			except Exception as ee:
				logger.warning(f"Failed export CSV for {path.name}: {ee}")
		return path, True, f"Written (applied: {applied}; downcast: {downcasted})", applied, downcasted
	except Exception as e:
		tb = traceback.format_exc(limit=2)
		return path, False, f"Processing failed: {e}\n{tb}", [], []


def main():
	args = parse_args()
	cache_dir = Path(args.cache_dir)
	if not cache_dir.exists():
		logger.error(f"Cache directory {cache_dir} does not exist.")
		sys.exit(1)
	parquet_files = discover_parquet_files(cache_dir)
	if args.limit:
		parquet_files = parquet_files[: args.limit]
	if not parquet_files:
		logger.warning("No parquet files found to process.")
		return
	timeframe_filters: Set[str] = set(args.timeframes) if args.timeframes else set()
	symbol_filters: Set[str] = set(args.tickers) if args.tickers else set()
	if symbol_filters:
		logger.info(f"Ticker filter active: {sorted(symbol_filters)}")
	logger.info(f"Starting indicator precompute on {len(parquet_files)} files (workers={args.workers})")
	export_dir = Path(args.export_enriched_dir) if args.export_enriched_dir else None
	results: List[Tuple[Path, bool, str, List[str], List[str]]] = []
	if args.workers and args.workers > 0:
		with ProcessPoolExecutor(max_workers=args.workers) as pool:
			futures = {
				pool.submit(
					process_file,
					p,
					symbol_filters,
					timeframe_filters,
					args.orb_start,
					args.orb_window,
					args.body_pct,
					args.force,
					args.dry_run,
					export_dir,
					args.downcast,
				): p
				for p in parquet_files
			}
			for fut in as_completed(futures):
				results.append(fut.result())
	else:
		for p in parquet_files:
			results.append(process_file(
				p,
				symbol_filters,
				timeframe_filters,
				args.orb_start,
				args.orb_window,
				args.body_pct,
				args.force,
				args.dry_run,
				export_dir,
				args.downcast,
			))
	# Summary
	# results entries are (path, ok, msg, applied, downcasted)
	successes = sum(1 for _path, ok, _msg, _applied, _downcasted in results if ok)
	logger.info(f"Indicator precompute complete. Success {successes}/{len(results)}")
	for path, ok, msg, applied, downcasted in results:
		level = "INFO" if ok else "WARNING"
		getattr(logger, level.lower())(f"{path.name}: {msg}")

	# Optional CSV report
	if args.report_file:
		report_path = Path(args.report_file)
		try:
			rows = []
			for path, ok, msg, applied, downcasted in results:
				symbol, timeframe = extract_symbol_timeframe(path)
				rows.append({
					"file": path.name,
					"symbol": symbol,
					"timeframe": timeframe,
					"status": "ok" if ok else "error",
					"applied_indicators": ",".join(applied),
					"downcast_columns": ",".join(downcasted),
					"message": msg,
				})
			pd.DataFrame(rows).to_csv(report_path, index=False)
			logger.info(f"Report written: {report_path}")
		except Exception as e:
			logger.warning(f"Failed to write report {report_path}: {e}")


if __name__ == "__main__":
	main()
