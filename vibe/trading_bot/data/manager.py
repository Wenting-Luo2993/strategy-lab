"""
Data manager orchestrating providers, cache, and aggregator.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from .aggregator import BarAggregator
from .cache import DataCache
from .providers.base import LiveDataProvider


logger = logging.getLogger(__name__)


class DataQualityCheck:
    """Result of a data quality check."""

    def __init__(
        self,
        passed: bool,
        check_name: str,
        message: str = "",
        details: Optional[dict] = None,
    ):
        """
        Initialize data quality check result.

        Args:
            passed: Whether check passed
            check_name: Name of the check
            message: Human-readable message
            details: Additional details dict
        """
        self.passed = passed
        self.check_name = check_name
        self.message = message
        self.details = details or {}


class DataManager:
    """
    Coordinates data providers, cache, and aggregator.

    Provides a unified interface for historical and real-time data with
    quality checks and event emission.
    """

    def __init__(
        self,
        provider: LiveDataProvider,
        cache_dir: Path,
        aggregator: Optional[BarAggregator] = None,
        cache_ttl_seconds: int = 3600,
    ):
        """
        Initialize data manager.

        Args:
            provider: Data provider for historical data
            cache_dir: Directory for cache storage
            aggregator: Bar aggregator (optional, for real-time)
            cache_ttl_seconds: Cache TTL in seconds
        """
        self.provider = provider
        self.cache = DataCache(cache_dir=cache_dir, ttl_seconds=cache_ttl_seconds)
        self.aggregator = aggregator
        self.cache_dir = cache_dir

        # Event handlers
        self._on_data_update: Optional[Callable] = None
        self._on_data_gap: Optional[Callable] = None
        self._on_quality_check: Optional[Callable] = None

        # Quality metrics
        self._total_fetches = 0
        self._cache_hits = 0
        self._data_gaps_detected = 0

        # Track real-time bars
        self._real_time_bars: Dict[str, pd.DataFrame] = {}

        logger.info("Initialized DataManager")

    def on_data_update(self, callback: Callable) -> None:
        """Register callback for data updates."""
        self._on_data_update = callback

    def on_data_gap(self, callback: Callable) -> None:
        """Register callback for data gaps."""
        self._on_data_gap = callback

    def on_quality_check(self, callback: Callable) -> None:
        """Register callback for quality checks."""
        self._on_quality_check = callback

    async def get_data(
        self,
        symbol: str,
        timeframe: str = "5m",
        days: int = 7,
        max_age_seconds: Optional[int] = None,
        allow_yfinance_fallback: bool = True,
    ) -> pd.DataFrame:
        """
        Get historical data for a symbol with smart caching.

        Implements intelligent cache management:
        - Historical data (previous days) is cached long-term (never changes)
        - Only fetches new data if needed, appending to existing cache
        - Checks cache first, falls back to provider, then updates cache

        Args:
            symbol: Trading symbol
            timeframe: Timeframe ('5m', '15m', '1h', etc.')
            days: Number of days of historical data (not used with smart caching)
            max_age_seconds: Maximum age of cache in seconds (uses default TTL if None)
            allow_yfinance_fallback: Whether to fall back to yfinance when cache is stale.
                Set to False during market hours to prevent 15-min delayed data from
                interfering with real-time Finnhub websocket data.

        Returns:
            DataFrame with OHLCV data
        """
        symbol = symbol.upper().strip()

        # Check cache first (before logging, to avoid confusion)
        cached_df = self.cache.get(symbol, timeframe)
        existing_cached_df = None  # Will be set if cache is stale or expired

        if cached_df is not None and len(cached_df) > 0:
            # Check if the last bar is stale (for intraday trading, we need fresh data)
            # Timeframe to minutes mapping
            timeframe_minutes = {
                "1m": 1, "5m": 5, "15m": 15, "30m": 30,
                "1h": 60, "1d": 1440
            }
            interval_minutes = timeframe_minutes.get(timeframe, 5)

            # Check if last bar timestamp is stale (older than 2x the interval)
            if "timestamp" in cached_df.columns and not cached_df.empty:
                last_bar_time = pd.to_datetime(cached_df.iloc[-1]["timestamp"])
                now = datetime.now()

                # Make last_bar_time timezone-aware if it isn't already
                if last_bar_time.tzinfo is None:
                    import pytz
                    last_bar_time = pytz.utc.localize(last_bar_time)
                if now.tzinfo is None:
                    import pytz
                    now = pytz.utc.localize(now)

                age_minutes = (now - last_bar_time).total_seconds() / 60
                staleness_threshold = interval_minutes * 2  # 2x the interval

                if age_minutes > staleness_threshold:
                    logger.info(
                        f"[CACHE STALE] {symbol} ({timeframe}): "
                        f"Last bar is {age_minutes:.1f} minutes old (threshold: {staleness_threshold} min), "
                        f"will fetch and append new data"
                    )
                    # Treat cached data as expired, will fetch and append below
                    existing_cached_df = cached_df
                    cached_df = None  # Force cache miss to trigger fetch
                else:
                    self._cache_hits += 1
                    # Log cache hit at INFO level so it's visible
                    logger.info(
                        f"[CACHE HIT] {symbol} ({timeframe}): "
                        f"Returning {len(cached_df)} cached rows "
                        f"(last bar: {age_minutes:.1f} min ago, TTL: {self.cache.ttl_seconds}s = {self.cache.ttl_seconds//86400} days)"
                    )

                    # Still emit update event
                    if self._on_data_update:
                        await self._on_data_update({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "source": "cache",
                            "rows": len(cached_df),
                        })

                    return cached_df
            else:
                # No timestamp column, return cached data as-is
                self._cache_hits += 1
                logger.info(
                    f"[CACHE HIT] {symbol} ({timeframe}): "
                    f"Returning {len(cached_df)} cached rows (TTL: {self.cache.ttl_seconds}s = {self.cache.ttl_seconds//86400} days)"
                )

                if self._on_data_update:
                    await self._on_data_update({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "source": "cache",
                        "rows": len(cached_df),
                    })

                return cached_df

        # Cache miss or stale cache - check if we have expired cached data we can append to
        if cached_df is None and existing_cached_df is None:
            cache_metadata = self.cache.get_metadata(symbol, timeframe)

            if cache_metadata is not None:
                # We have cached data but it's expired - let's try to append new data
                cache_path = self.cache._get_cache_path(symbol, timeframe)
                try:
                    if cache_path.exists():
                        existing_cached_df = pd.read_parquet(cache_path)
                        if not existing_cached_df.empty:
                            logger.info(
                                f"[CACHE EXPIRED] {symbol} ({timeframe}): "
                                f"Found {len(existing_cached_df)} expired cached rows, will append new data"
                            )
                except Exception as e:
                    logger.warning(f"Error reading expired cache for {symbol}/{timeframe}: {e}")
                    existing_cached_df = None

        # Check if yfinance fallback is allowed
        if not allow_yfinance_fallback:
            # Return existing stale cache if we have it, otherwise empty DataFrame
            if existing_cached_df is not None and not existing_cached_df.empty:
                logger.debug(
                    f"[NO FALLBACK] {symbol} ({timeframe}): Cache is stale but yfinance fallback disabled (expected during market hours). "
                    f"Returning {len(existing_cached_df)} rows of stale cached data, waiting for real-time Finnhub bars..."
                )
                return existing_cached_df
            else:
                logger.debug(
                    f"[NO FALLBACK] {symbol} ({timeframe}): Cache is stale but yfinance fallback disabled (expected during market hours). "
                    f"No cached data available, returning empty DataFrame, waiting for real-time Finnhub bars..."
                )
                return pd.DataFrame()

        # Fetch from provider
        logger.info(f"[CACHE MISS] {symbol} ({timeframe}): Fetching from yfinance...")

        self._total_fetches += 1

        try:
            # Use period-based fetching (more reliable than explicit date ranges)
            # Passing None for both dates triggers period-based fetching in provider
            df = await self.provider.get_bars(
                symbol=symbol,
                timeframe=timeframe,
                limit=None,
                start_time=None,  # None = use period-based fetching
                end_time=None,    # None = use period-based fetching
            )

            if df.empty:
                logger.warning(f"No data fetched for {symbol}/{timeframe}")
                # Return existing cached data if we have it, even if expired
                if existing_cached_df is not None and not existing_cached_df.empty:
                    logger.info(f"Returning {len(existing_cached_df)} expired cached rows as fallback")
                    return existing_cached_df
                return pd.DataFrame()

            # If we have existing cached data, merge it intelligently
            if existing_cached_df is not None and not existing_cached_df.empty:
                # Combine old cached data with new fetched data
                combined_df = pd.concat([existing_cached_df, df], ignore_index=True)

                # Remove duplicates based on timestamp (prefer newer data)
                if "timestamp" in combined_df.columns:
                    combined_df = combined_df.drop_duplicates(subset=["timestamp"], keep="last")
                    combined_df = combined_df.sort_values("timestamp").reset_index(drop=True)

                logger.info(
                    f"[MERGE] {symbol} ({timeframe}): "
                    f"Merged {len(existing_cached_df)} cached + {len(df)} new = {len(combined_df)} total rows"
                )
                df = combined_df

            # Run quality checks
            quality_checks = await self._run_quality_checks(symbol, timeframe, df)

            # Cache the data
            self.cache.put(symbol, timeframe, df)
            logger.info(
                f"[YFINANCE] {symbol} ({timeframe}): "
                f"Fetched {len(df)} rows from yfinance, cached for {self.cache.ttl_seconds}s ({self.cache.ttl_seconds//86400} days)"
            )

            # Emit update event
            if self._on_data_update:
                await self._on_data_update({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source": "provider",
                    "rows": len(df),
                })

            return df

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}/{timeframe}: {e}")
            # Return existing cached data if we have it, even if expired
            if existing_cached_df is not None and not existing_cached_df.empty:
                logger.info(f"Returning {len(existing_cached_df)} expired cached rows as fallback after error")
                return existing_cached_df
            raise

    async def add_real_time_bar(
        self,
        symbol: str,
        timeframe: str,
        bar: dict,
    ) -> None:
        """
        Add a real-time bar to the data manager.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            bar: Bar dict with OHLCV data
        """
        symbol = symbol.upper().strip()

        # Convert bar dict to DataFrame row if needed
        if isinstance(bar, dict):
            bar_df = pd.DataFrame([bar])
        else:
            bar_df = bar

        # Track real-time bars with memory limit
        key = f"{symbol}_{timeframe}"
        MAX_REALTIME_BARS = 1000  # Keep last 1000 bars per symbol/timeframe

        if key not in self._real_time_bars:
            self._real_time_bars[key] = bar_df
        else:
            self._real_time_bars[key] = pd.concat(
                [self._real_time_bars[key], bar_df],
                ignore_index=True
            )
            # Trim to prevent unbounded growth
            if len(self._real_time_bars[key]) > MAX_REALTIME_BARS:
                self._real_time_bars[key] = self._real_time_bars[key].tail(MAX_REALTIME_BARS)

        # Emit update event
        if self._on_data_update:
            await self._on_data_update({
                "symbol": symbol,
                "timeframe": timeframe,
                "source": "real-time",
                "bar": bar,
            })

    async def get_merged_data(
        self,
        symbol: str,
        timeframe: str,
        days: int = 7,
    ) -> pd.DataFrame:
        """
        Get merged historical + real-time data.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            days: Number of days of historical data

        Returns:
            DataFrame with combined historical and real-time data
        """
        # Get historical data
        historical = await self.get_data(symbol, timeframe, days)

        # Get real-time data if available
        key = f"{symbol}_{timeframe}"
        real_time = self._real_time_bars.get(key, pd.DataFrame())

        if real_time.empty:
            return historical

        # Merge data
        merged = pd.concat([historical, real_time], ignore_index=True)

        # Remove duplicates based on timestamp (prefer real-time)
        if "timestamp" in merged.columns:
            merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
            merged = merged.sort_values("timestamp").reset_index(drop=True)

        return merged

    async def _run_quality_checks(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
    ) -> List[DataQualityCheck]:
        """
        Run data quality checks on fetched data.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            df: DataFrame to check

        Returns:
            List of quality check results
        """
        checks = []

        # Check 1: Data not empty
        check1 = DataQualityCheck(
            passed=not df.empty,
            check_name="not_empty",
            message="Data is empty" if df.empty else "Data contains rows",
        )
        checks.append(check1)

        # Check 2: Required columns present
        required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
        available_cols = set(df.columns)
        missing_cols = required_cols - available_cols

        check2 = DataQualityCheck(
            passed=len(missing_cols) == 0,
            check_name="required_columns",
            message=f"Missing columns: {missing_cols}" if missing_cols else "All required columns present",
            details={"missing": list(missing_cols)},
        )
        checks.append(check2)

        # Check 3: No NaN values in critical columns
        critical_cols = ["open", "high", "low", "close", "volume"]
        nan_cols = [col for col in critical_cols if col in df.columns and df[col].isna().any()]

        check3 = DataQualityCheck(
            passed=len(nan_cols) == 0,
            check_name="no_nans",
            message=f"Columns with NaNs: {nan_cols}" if nan_cols else "No NaN values in critical columns",
            details={"nan_columns": nan_cols},
        )
        checks.append(check3)

        # Check 4: OHLC relationships (high >= max(open,close), low <= min(open,close))
        if "high" in df.columns and "low" in df.columns:
            invalid_ohlc = df[
                (df["high"] < df["open"]) |
                (df["high"] < df["close"]) |
                (df["low"] > df["open"]) |
                (df["low"] > df["close"]) |
                (df["high"] < df["low"])
            ]

            check4 = DataQualityCheck(
                passed=len(invalid_ohlc) == 0,
                check_name="ohlc_relationships",
                message=f"Invalid OHLC rows: {len(invalid_ohlc)}" if len(invalid_ohlc) > 0 else "All OHLC relationships valid",
                details={"invalid_count": len(invalid_ohlc)},
            )
            checks.append(check4)

        # Check 5: Gap detection (missing bars)
        if "timestamp" in df.columns and len(df) > 1:
            df_sorted = df.sort_values("timestamp")
            timestamps = pd.to_datetime(df_sorted["timestamp"])
            time_diffs = timestamps.diff()

            # Convert interval to timedelta
            interval_map = {
                "1m": timedelta(minutes=1),
                "5m": timedelta(minutes=5),
                "15m": timedelta(minutes=15),
                "30m": timedelta(minutes=30),
                "1h": timedelta(hours=1),
                "4h": timedelta(hours=4),
                "1d": timedelta(days=1),
            }

            expected_interval = interval_map.get(timeframe, timedelta(minutes=5))
            gaps = time_diffs[time_diffs > expected_interval * 1.5]

            if len(gaps) > 0:
                self._data_gaps_detected += 1
                check5 = DataQualityCheck(
                    passed=False,
                    check_name="gap_detection",
                    message=f"Detected {len(gaps)} gaps in data",
                    details={"gap_count": len(gaps)},
                )

                if self._on_data_gap:
                    # Handle both sync and async callbacks
                    import asyncio
                    if asyncio.iscoroutinefunction(self._on_data_gap):
                        await self._on_data_gap({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "gap_count": len(gaps),
                            "gaps": [float(g.total_seconds()) for g in gaps],
                        })
                    else:
                        self._on_data_gap({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "gap_count": len(gaps),
                            "gaps": [float(g.total_seconds()) for g in gaps],
                        })
            else:
                check5 = DataQualityCheck(
                    passed=True,
                    check_name="gap_detection",
                    message="No gaps detected",
                )

            checks.append(check5)

        # Emit quality check results
        if self._on_quality_check:
            import asyncio
            for check in checks:
                if asyncio.iscoroutinefunction(self._on_quality_check):
                    await self._on_quality_check({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "check_name": check.check_name,
                        "passed": check.passed,
                        "message": check.message,
                        "details": check.details,
                    })
                else:
                    self._on_quality_check({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "check_name": check.check_name,
                        "passed": check.passed,
                        "message": check.message,
                        "details": check.details,
                    })

        return checks

    def get_metrics(self) -> dict:
        """
        Get data manager metrics.

        Returns:
            Dictionary with metrics
        """
        total_requests = self._total_fetches

        return {
            "total_fetches": self._total_fetches,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": (
                (self._cache_hits / total_requests * 100)
                if total_requests > 0
                else 0
            ),
            "data_gaps_detected": self._data_gaps_detected,
            "cache_stats": self.cache.stats(),
            "provider_health": self.provider.get_health_status(),
        }

    def reset_metrics(self) -> None:
        """Reset metrics counters."""
        self._total_fetches = 0
        self._cache_hits = 0
        self._data_gaps_detected = 0
