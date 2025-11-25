# src/data/cache.py
import os
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pandas as pd

from src.data.base import DataLoader
from src.utils.logger import get_logger
from src.utils.google_drive_sync import DriveSync
from src.utils.workspace import resolve_workspace_path
from src.indicators.incremental import IncrementalIndicatorEngine
from src.config.indicators import CORE_INDICATORS, ORB_DEFAULT_PARAMS

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
            * Preserves any pre-computed indicator columns (e.g. EMA_*, RSI_*, ATR*, ORB_*) that were
                added offline (via apply_indicator_data_cache.py) when reading and merging cache content.
                New fetched segments that lack those columns will simply have NaN values for them; the
                loader does NOT drop or recalculate indicator fields.
            * Uses centralized workspace path anchoring (src.utils.workspace) so that relative cache_dir
                values are always resolved against the project python/ root regardless of the caller's CWD.
    """

    def __init__(
        self,
        wrapped_loader: Optional[DataLoader] = None,
        cache_dir: Union[str, Path] = "data_cache",
        max_lookback_days: int = 59,
        timeframe_minutes_map: Optional[dict] = None,
        cloud_sync: bool = False,
        drive_root: str = "strategy-lab",
        use_service_account: bool = True,
        service_account_env: str = "GOOGLE_SERVICE_ACCOUNT_KEY",
        auto_indicators: Optional[List[str]] = None,
        indicator_mode: str = "incremental",
    ):
        """Initialize CacheDataLoader with optional incremental indicator calculation.

        Args:
            wrapped_loader: Underlying data loader (None = cache-only mode)
            cache_dir: Cache directory path
            max_lookback_days: Maximum lookback window for new fetches
            timeframe_minutes_map: Map timeframe strings to minutes
            cloud_sync: Enable Google Drive sync
            drive_root: Drive root folder name
            use_service_account: Use service account for Drive auth
            service_account_env: Environment variable for service account key
            auto_indicators: List of indicators to calculate automatically (None = CORE_INDICATORS)
            indicator_mode: How to handle indicators:
                - 'incremental': Use IncrementalIndicatorEngine for efficient updates (default)
                - 'batch': Recalculate all indicators on every fetch (legacy behavior)
                - 'skip': Don't calculate indicators, preserve existing values only
        """
        # Underlying loader is optional; if None we operate in cache-only mode
        self.wrapped_loader = wrapped_loader
        # Anchor relative cache_dir paths using shared workspace utility
        cd = resolve_workspace_path(cache_dir, start=__file__)
        self.cache_dir = cd
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
        # Cloud sync utility (lazy enable)
        self._drive_sync = DriveSync(
            enable=cloud_sync,
            root_folder=drive_root,
            service_account_env=service_account_env,
            use_service_account=use_service_account,
        ) if cloud_sync else None

        # Indicator configuration
        self.indicator_mode = indicator_mode
        if auto_indicators is None and indicator_mode != 'skip':
            self.auto_indicators = CORE_INDICATORS
        else:
            self.auto_indicators = auto_indicators or []

        # Initialize incremental indicator engine if needed
        if indicator_mode == 'incremental' and self.auto_indicators:
            self.indicator_engine = IncrementalIndicatorEngine()
            logger.info(f"Initialized incremental indicator engine with {len(self.auto_indicators)} indicators: {self.auto_indicators}")
        else:
            self.indicator_engine = None

    # ---------------------- Path & Migration Helpers ---------------------- #
    def _rolling_cache_path(self, symbol: str, timeframe: str) -> Path:
        clean_symbol = symbol.replace(":", "_").replace("/", "_")
        return self.cache_dir / f"{clean_symbol}_{timeframe}.parquet"

    def _indicator_state_path(self, symbol: str, timeframe: str) -> Path:
        """Get path to indicator state file for this symbol/timeframe."""
        clean_symbol = symbol.replace(":", "_").replace("/", "_")
        return self.cache_dir / f"{clean_symbol}_{timeframe}_indicators.pkl"

    @staticmethod
    def _parse_indicator_config(indicator_str: str) -> dict:
        """Parse indicator string into configuration dictionary.

        Examples:
            "EMA_9" -> {'name': 'ema', 'params': {'length': 9}, 'column': 'EMA_9'}
            "RSI_14" -> {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'}
            "ATR_14" -> {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'}
            "MACD_12_26_9" -> {'name': 'macd', 'params': {'fast': 12, 'slow': 26, 'signal': 9}, 'column': 'MACD_12_26_9'}
            "bbands_20_2" -> {'name': 'bbands', 'params': {'length': 20, 'std': 2.0}, 'column': 'bbands_20_2'}
            "orb_levels" -> {'name': 'orb_levels', 'params': {}, 'column': 'orb_levels'}
            "SMA_20" -> {'name': 'sma', 'params': {'length': 20}, 'column': 'SMA_20'}
        """
        parts = indicator_str.split("_")
        name = parts[0].lower()

        # Handle special cases
        if name == "macd" and len(parts) == 4:
            return {
                'name': 'macd',
                'params': {'fast': int(parts[1]), 'slow': int(parts[2]), 'signal': int(parts[3])},
                'column': indicator_str
            }
        elif name == "bbands" and len(parts) == 3:
            return {
                'name': 'bbands',
                'params': {'length': int(parts[1]), 'std': float(parts[2])},
                'column': indicator_str
            }
        elif name == "orb":
            # ORB with default parameters from centralized config
            return {
                'name': 'orb_levels',
                'params': ORB_DEFAULT_PARAMS.copy(),
                'column': 'orb_levels'
            }
        elif name == "atr" and len(parts) == 2:
            # ATR uses ATRr_ prefix for column name
            return {
                'name': 'atr',
                'params': {'length': int(parts[1])},
                'column': f'ATRr_{parts[1]}'
            }
        elif len(parts) == 2:
            # Standard format: INDICATOR_LENGTH
            return {
                'name': name,
                'params': {'length': int(parts[1])},
                'column': indicator_str
            }
        else:
            raise ValueError(f"Unsupported indicator format: {indicator_str}")

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
        logger.info(f"Fetch called for {symbol} {timeframe} start={start} end={end}")

        # Migrate any legacy files on first use
        self._migrate_legacy(symbol, timeframe)

        # Normalize dates
        # Use UTC now when end not specified
        end_dt = self._parse_date(end) if end else datetime.now(timezone.utc)

        # Read cache to check earliest available date if start is None
        rolling_path = self._rolling_cache_path(symbol, timeframe)
        # Cloud pre-sync (download newer remote copy if exists)
        if self._drive_sync and self._drive_sync.enable:
            remote_rel = f"data_cache/{rolling_path.name}"
            try:
                self._drive_sync.sync_down(rolling_path, remote_rel)
            except Exception as e:  # pragma: no cover
                logger.warning(f"Drive sync_down failed for {rolling_path.name}: {e}")

        df_cache = self._read_cache(rolling_path)
        logger.debug(f"Cache read for {symbol} {timeframe} from {rolling_path}: rows={len(df_cache)}")

        # Load indicator state if in incremental mode
        state_loaded = False
        if self.indicator_mode == 'incremental' and self.indicator_engine:
            state_path = self._indicator_state_path(symbol, timeframe)
            if state_path.exists():
                try:
                    self.indicator_engine.load_state(state_path)
                    state_loaded = True
                    logger.debug(f"Loaded indicator state from {state_path.name}")
                except Exception as e:  # pragma: no cover
                    logger.warning(f"Failed to load indicator state from {state_path.name}: {e}. Starting fresh.")

        if start is None and not df_cache.empty:
            # Derive earliest date only if we truly have a DatetimeIndex
            if isinstance(df_cache.index, pd.DatetimeIndex):
                start_dt = df_cache.index.min().date()
                logger.info(
                    f"No start date provided. Using earliest cache date (datetime index detected): {start_dt}"
                )
            else:
                raise ValueError("Start date is None but cache index is not DatetimeIndex; cannot derive start date.")
        else:
            # Use provided start date or default to max lookback from end
            start_dt = self._parse_date(start) if start else end_dt - timedelta(days=self.max_lookback_days)

        # Cache was already read above for start date determination
        # No need to read it again

        # Determine clamp boundary for new fetches
        # Ensure we compare date objects (end_dt may be datetime if provided that way)
        if isinstance(end_dt, datetime):
            end_date_only = end_dt.date()
        else:
            end_date_only = end_dt
        clamp_start_dt = end_date_only - timedelta(days=self.max_lookback_days)
        # Normalize start_dt to a date object if it's a datetime to avoid TypeError comparing
        # datetime.datetime to datetime.date (observed in production).
        if isinstance(start_dt, datetime):
            logger.debug(f"start_dt is datetime; converting to date component for comparison: {start_dt}")
            start_dt_date = start_dt.date()
        else:
            start_dt_date = start_dt
        fetchable_start_dt = max(start_dt_date, clamp_start_dt)
        logger.debug(
            f"Fetchable start date after applying lookback clamp (clamp_start={clamp_start_dt}, raw_start={start_dt_date}): {fetchable_start_dt}"
        )

        # Compute missing segments relative to cache
        missing_segments: List[Tuple[datetime, datetime]] = []

        if df_cache.empty:
            # Entire requested window considered missing but we only fetch from fetchable_start_dt
            if fetchable_start_dt <= end_date_only:
                missing_segments.append((fetchable_start_dt, end_date_only))
                logger.debug("Cache empty; entire fetchable range is missing")
        else:
            cache_start = df_cache.index.min().date()
            cache_end = df_cache.index.max().date()
            logger.debug(f"Cache received. Cache range: {cache_start} to {cache_end}")

            # Left side missing (only fetch if after clamp boundary)
            if fetchable_start_dt < cache_start:
                left_end = min(cache_start - timedelta(days=1), end_date_only)
                if fetchable_start_dt <= left_end:
                    missing_segments.append((fetchable_start_dt, left_end))

            # Right side missing
            if end_date_only > cache_end:
                right_start = max(fetchable_start_dt, cache_end + timedelta(days=1))
                if right_start <= end_date_only:
                    missing_segments.append((right_start, end_date_only))

            # Middle gaps (daily-level detection). For intraday, we rely on continuity later.
            # If timeframe_minutes available, we could refine; skipping for now to limit complexity.

        # Coalesce overlapping/adjacent missing segments (simple since day granularity)
        missing_segments = self._coalesce_segments(missing_segments)
        logger.info(f"Fetching data for {symbol} {timeframe} from {start_dt} to {end_dt}. Missing segments: {missing_segments}")

        # Fetch missing segments
        new_frames = []
        if self.wrapped_loader is None and missing_segments:
            logger.warning(
                f"Cache-only mode: missing segments for {symbol} {timeframe} not fetched because wrapped_loader is None. Returning cached subset."
            )
        else:
            for seg_start, seg_end in missing_segments:
                try:
                    seg_df = self._fetch_segment(symbol, seg_start, seg_end)
                    if not seg_df.empty:
                        new_frames.append(seg_df)
                        logger.debug(
                            f"Fetched segment {symbol} {timeframe} {seg_start} -> {seg_end} rows={len(seg_df)}"
                        )
                        logger.info("Succesfully fetched segment")
                except Exception as e:  # pragma: no cover - defend
                    logger.error(
                        f"Error fetching segment for {symbol} {timeframe} {seg_start} -> {seg_end}: {e}"
                    )

        # Merge and persist if we have new data
        if new_frames:
            # Track old cache size to determine where new data starts
            old_cache_size = len(df_cache)

            combined = pd.concat([df_cache] + new_frames, axis=0) if not df_cache.empty else pd.concat(new_frames, axis=0)
            combined = self._standardize_df(combined)
            combined = self._dedupe_and_sort(combined)

            # Calculate indicators incrementally on new data if configured
            if self.indicator_mode == 'incremental' and self.indicator_engine and self.auto_indicators:
                try:
                    # Determine where new data starts in the combined dataframe
                    # After deduplication, the boundary might have shifted slightly
                    new_start_idx = old_cache_size if old_cache_size > 0 else 0

                    # Only calculate if we have new bars to process
                    if new_start_idx < len(combined):
                        # Parse indicator strings into configuration dicts
                        indicator_configs = [self._parse_indicator_config(ind) for ind in self.auto_indicators]

                        logger.debug(f"Calculating {len(indicator_configs)} indicators incrementally starting at index {new_start_idx}")
                        combined = self.indicator_engine.update(
                            df=combined,
                            new_start_idx=new_start_idx,
                            indicators=indicator_configs,
                            symbol=symbol,
                            timeframe=timeframe
                        )
                        logger.debug(f"Incremental indicator calculation complete for {len(combined) - new_start_idx} new bars")

                        # Save indicator state after successful calculation
                        state_path = self._indicator_state_path(symbol, timeframe)
                        try:
                            self.indicator_engine.save_state(state_path)
                            logger.debug(f"Saved indicator state to {state_path.name}")
                        except Exception as e:  # pragma: no cover
                            logger.warning(f"Failed to save indicator state to {state_path.name}: {e}")
                except Exception as e:  # pragma: no cover
                    logger.error(f"Failed to calculate indicators incrementally: {e}. Continuing without indicators.")

            # Save
            combined.to_parquet(rolling_path)
            df_cache = combined
            logger.debug(
                f"Updated rolling cache {rolling_path.name}: total rows={len(df_cache)} (added {sum(len(f) for f in new_frames)})"
            )
        else:
            # No new data fetched, but check if we need to initialize state from existing cache
            logger.debug(f"No new frames fetched. Checking state initialization: mode={self.indicator_mode}, engine={self.indicator_engine is not None}, indicators={len(self.auto_indicators) if self.auto_indicators else 0}, cache_empty={df_cache.empty}, state_loaded={state_loaded}")
            if self.indicator_mode == 'incremental' and self.indicator_engine and self.auto_indicators and not df_cache.empty and not state_loaded:
                state_path = self._indicator_state_path(symbol, timeframe)
                logger.info(f"No state file found but cache exists. Initializing state from {len(df_cache)} cached bars.")
                try:
                    # Calculate indicators on entire cache to build initial state
                    indicator_configs = [self._parse_indicator_config(ind) for ind in self.auto_indicators]
                    df_cache = self.indicator_engine.update(
                        df=df_cache,
                        new_start_idx=0,  # Process all cached data
                        indicators=indicator_configs,
                        symbol=symbol,
                        timeframe=timeframe
                    )
                    # Save the newly built state
                    logger.info(f"About to save indicator state to {state_path}")
                    self.indicator_engine.save_state(state_path)
                    logger.info(f"Successfully initialized and saved indicator state to {state_path.name}")                    # Update the parquet cache with calculated indicators
                    df_cache.to_parquet(rolling_path)
                    logger.debug(f"Updated cache with indicators: {rolling_path.name}")
                except Exception as e:  # pragma: no cover
                    logger.error(f"Failed to initialize indicator state: {e}")

            if df_cache.empty:
                logger.warning(
                    f"No data fetched and cache empty for {symbol} {timeframe}; returning empty DataFrame"
                )

        # Slice to requested user window (even if earlier portion was clamped we don't attempt to fabricate)
        if isinstance(df_cache.index, pd.DatetimeIndex):
            requested_slice = df_cache[(df_cache.index.date >= start_dt_date) & (df_cache.index.date <= end_date_only)]
        else:
            logger.warning(
                "CacheDataLoader.fetch: cache index is not DatetimeIndex; returning full cache without date slice"
            )
            requested_slice = df_cache
        # Cloud post-sync (upload if changed)
        if self._drive_sync and self._drive_sync.enable and rolling_path.exists():
            try:
                remote_rel = f"data_cache/{rolling_path.name}"
                self._drive_sync.sync_up(rolling_path, remote_rel)
            except Exception as e:  # pragma: no cover
                logger.warning(f"Drive sync_up failed for {rolling_path.name}: {e}")
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
                logger.debug(
                    f"_read_cache: reading {path.name} size={path.stat().st_size} bytes"
                )
                df = pd.read_parquet(path)
                # NOTE: We return all columns as-is (including any indicator / feature columns
                # previously enriched and persisted). _standardize_df only adjusts index & order.
                return self._standardize_df(df)
            except Exception as e:  # pragma: no cover
                logger.error(f"Failed reading cache {path}: {e}")
        return pd.DataFrame()

    def _fetch_segment(self, symbol: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
        if self.wrapped_loader is None:
            # Cache-only mode: no fetch possible
            logger.debug(f"_fetch_segment skipped (cache-only mode) for {symbol} {start}->{end}")
            return pd.DataFrame()
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
