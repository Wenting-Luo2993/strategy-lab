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
    ) -> pd.DataFrame:
        """
        Get historical data for a symbol.

        Checks cache first, falls back to provider, then updates cache.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe ('5m', '15m', '1h', etc.)
            days: Number of days of historical data
            max_age_seconds: Maximum age of cache in seconds (uses default TTL if None)

        Returns:
            DataFrame with OHLCV data
        """
        symbol = symbol.upper().strip()

        logger.info(f"Getting data for {symbol} ({timeframe}, {days} days)")

        # Check cache first
        cached_df = self.cache.get(symbol, timeframe)

        if cached_df is not None and len(cached_df) > 0:
            self._cache_hits += 1
            logger.debug(f"Cache hit for {symbol}/{timeframe}")

            # Still emit update event
            if self._on_data_update:
                await self._on_data_update({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source": "cache",
                    "rows": len(cached_df),
                })

            return cached_df

        # Cache miss - fetch from provider
        logger.debug(f"Cache miss for {symbol}/{timeframe}, fetching from provider")

        self._total_fetches += 1

        try:
            df = await self.provider.get_bars(
                symbol=symbol,
                timeframe=timeframe,
                limit=None,
                end_time=datetime.now(),
                start_time=datetime.now() - timedelta(days=days),
            )

            if df.empty:
                logger.warning(f"No data fetched for {symbol}/{timeframe}")
                return pd.DataFrame()

            # Run quality checks
            quality_checks = await self._run_quality_checks(symbol, timeframe, df)

            # Cache the data
            self.cache.put(symbol, timeframe, df)

            # Emit update event
            if self._on_data_update:
                await self._on_data_update({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source": "provider",
                    "rows": len(df),
                })

            logger.info(f"Fetched {len(df)} rows for {symbol}/{timeframe}")

            return df

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}/{timeframe}: {e}")
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

        # Track real-time bars
        key = f"{symbol}_{timeframe}"

        if key not in self._real_time_bars:
            self._real_time_bars[key] = bar_df
        else:
            self._real_time_bars[key] = pd.concat(
                [self._real_time_bars[key], bar_df],
                ignore_index=True
            )

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
