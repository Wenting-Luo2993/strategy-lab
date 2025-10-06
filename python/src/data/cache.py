# src/data/cache.py
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pandas as pd

from src.data.base import DataLoader
from src.utils.logger import get_logger

# Get a configured logger for this module
logger = get_logger("Cache")


class CacheDataLoader(DataLoader):
    """A data loader wrapper adding intelligent rolling caching with partial fetch.

    Features:
      * Maintains a single rolling parquet file per (symbol,timeframe) accumulating history.
      * If requested range partially overlaps cache, fetches only missing segments.
      * Enforces a maximum lookback window (default 59 days) for NEW fetches while still serving
        any older data already cached.
      * Migrates legacy date-ranged cache files (old format with start/end in filename) into the
        new rolling file automatically on first use.
    """

    def __init__(
        self,
        wrapped_loader: DataLoader,
        cache_dir: str = "data_cache",
        max_lookback_days: int = 59,
        timeframe_minutes_map: Optional[dict] = None,
    ):
        self.wrapped_loader = wrapped_loader
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_lookback_days = max_lookback_days
        # Map timeframe string to minutes (used for gap detection). Extend as needed.
        self.timeframe_minutes_map = timeframe_minutes_map or {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "1h": 60,
            "1d": 60 * 24,
        }

    # ---------------------- Path & Migration Helpers ---------------------- #
    def _rolling_cache_path(self, symbol: str, timeframe: str) -> Path:
        clean_symbol = symbol.replace(":", "_").replace("/", "_")
        return self.cache_dir / f"{clean_symbol}_{timeframe}.parquet"

    def _legacy_files(self, symbol: str, timeframe: str) -> List[Path]:
        clean_symbol = symbol.replace(":", "_").replace("/", "_")
        pattern = f"{clean_symbol}_{timeframe}_"  # prefix of legacy files
        return [p for p in self.cache_dir.glob(f"{clean_symbol}_{timeframe}_*.parquet") if pattern in p.name]

    def _migrate_legacy(self, symbol: str, timeframe: str) -> None:
        rolling_path = self._rolling_cache_path(symbol, timeframe)
        if rolling_path.exists():
            return  # Nothing to do
        legacy = self._legacy_files(symbol, timeframe)
        if not legacy:
            return
        frames = []
        for file in legacy:
            try:
                frames.append(pd.read_parquet(file))
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Failed to read legacy cache file {file}: {e}")
        if not frames:
            return
        merged = pd.concat(frames, axis=0)
        merged = self._standardize_df(merged)
        merged = self._dedupe_and_sort(merged)
        merged.to_parquet(rolling_path)
        for file in legacy:
            if file.exists():
                try:
                    file.unlink()
                except OSError as e:  # pragma: no cover
                    logger.warning(f"Failed to remove legacy file {file}: {e}")
        logger.info(
            f"Migrated {len(legacy)} legacy cache files for {symbol} {timeframe} into {rolling_path.name}"
        )

    # ---------------------- Core Public Method ---------------------- #
    def fetch(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[Union[str, date, datetime]] = None,
        end: Optional[Union[str, date, datetime]] = None,
    ) -> pd.DataFrame:
        """Fetch data with rolling cache, partial fetch and lookback clamp.

        Args:
            symbol: Ticker symbol
            timeframe: e.g. '5m'
            start: inclusive start date as YYYY-MM-DD string, date, datetime or None
            end: inclusive end date as YYYY-MM-DD string, date, datetime or None
        Returns:
            DataFrame indexed by datetime containing at least requested slice (clamped when beyond lookback).
        """
        # Normalize dates
        end_dt = self._parse_date(end) if end else datetime.utcnow().date()
        start_dt = self._parse_date(start) if start else end_dt - timedelta(days=min(30, self.max_lookback_days))

        # Migrate any legacy files on first use
        self._migrate_legacy(symbol, timeframe)

        rolling_path = self._rolling_cache_path(symbol, timeframe)
        df_cache = self._read_cache(rolling_path)

        # Determine clamp boundary for new fetches
        clamp_start_dt = end_dt - timedelta(days=self.max_lookback_days)
        fetchable_start_dt = max(start_dt, clamp_start_dt)

        # Compute missing segments relative to cache
        missing_segments: List[Tuple[datetime, datetime]] = []
        timeframe_minutes = self.timeframe_minutes_map.get(timeframe)

        if df_cache.empty:
            # Entire requested window considered missing but we only fetch from fetchable_start_dt
            if fetchable_start_dt <= end_dt:
                missing_segments.append((fetchable_start_dt, end_dt))
        else:
            cache_start = df_cache.index.min().date()
            cache_end = df_cache.index.max().date()

            # Left side missing (only fetch if after clamp boundary)
            if fetchable_start_dt < cache_start:
                left_end = min(cache_start - timedelta(days=1), end_dt)
                if fetchable_start_dt <= left_end:
                    missing_segments.append((fetchable_start_dt, left_end))

            # Right side missing
            if end_dt > cache_end:
                right_start = max(fetchable_start_dt, cache_end + timedelta(days=1))
                if right_start <= end_dt:
                    missing_segments.append((right_start, end_dt))

            # Middle gaps (daily-level detection). For intraday, we rely on continuity later.
            # If timeframe_minutes available, we could refine; skipping for now to limit complexity.

        # Coalesce overlapping/adjacent missing segments (simple since day granularity)
        missing_segments = self._coalesce_segments(missing_segments)

        # Fetch missing segments
        new_frames = []
        for seg_start, seg_end in missing_segments:
            try:
                seg_df = self._fetch_segment(symbol, seg_start, seg_end)
                if not seg_df.empty:
                    new_frames.append(seg_df)
                    logger.debug(
                        f"Fetched segment {symbol} {timeframe} {seg_start} -> {seg_end} rows={len(seg_df)}"
                    )
            except Exception as e:  # pragma: no cover - defend
                logger.error(
                    f"Error fetching segment for {symbol} {timeframe} {seg_start} -> {seg_end}: {e}"
                )

        # Merge and persist if we have new data
        if new_frames:
            combined = pd.concat([df_cache] + new_frames, axis=0) if not df_cache.empty else pd.concat(new_frames, axis=0)
            combined = self._standardize_df(combined)
            combined = self._dedupe_and_sort(combined)
            # Save
            combined.to_parquet(rolling_path)
            df_cache = combined
            logger.debug(
                f"Updated rolling cache {rolling_path.name}: total rows={len(df_cache)} (added {sum(len(f) for f in new_frames)})"
            )
        else:
            if df_cache.empty:
                logger.warning(
                    f"No data fetched and cache empty for {symbol} {timeframe}; returning empty DataFrame"
                )

        # Slice to requested user window (even if earlier portion was clamped we don't attempt to fabricate)
        requested_slice = df_cache[(df_cache.index.date >= start_dt) & (df_cache.index.date <= end_dt)]
        return requested_slice

    # ---------------------- Internal Helpers ---------------------- #
    @staticmethod
    def _parse_date(d: Union[str, date, datetime]) -> date:
        """Parse input into a date.

        Accepts:
          * str in format YYYY-MM-DD
          * datetime.date
          * datetime.datetime (date component used)
        """
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, date):  # (and not datetime since datetime is subclass)
            return d
        if isinstance(d, str):
            return datetime.strptime(d, "%Y-%m-%d").date()
        raise TypeError(f"Unsupported date type: {type(d)}")

    def _read_cache(self, path: Path) -> pd.DataFrame:
        if path.exists():
            try:
                df = pd.read_parquet(path)
                return self._standardize_df(df)
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed reading cache {path}: {e}")
        return pd.DataFrame()

    def _fetch_segment(self, symbol: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
        # Underlying loader expects strings
        df: pd.DataFrame = self.wrapped_loader.fetch(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        return self._standardize_df(df)

    @staticmethod
    def _standardize_df(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        # Ensure datetime index (assume column named 'datetime' or existing index)
        if not isinstance(df.index, pd.DatetimeIndex):
            # Try common columns
            for c in ["datetime", "date", "time", "timestamp"]:
                if c in df.columns:
                    df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
                    df = df.set_index(c)
                    break
        # Force UTC if tz-naive
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df.sort_index()

    @staticmethod
    def _dedupe_and_sort(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df[~df.index.duplicated(keep="last")]
        return df.sort_index()

    @staticmethod
    def _coalesce_segments(segments: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
        if not segments:
            return []
        segments.sort(key=lambda x: x[0])
        merged = [segments[0]]
        for start, end in segments[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end + timedelta(days=1):  # adjacent or overlapping
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        return merged
