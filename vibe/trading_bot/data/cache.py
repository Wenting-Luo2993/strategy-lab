"""
Data cache manager for storing historical data with TTL-based invalidation.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd


logger = logging.getLogger(__name__)


class DataCache:
    """
    Local cache for OHLCV data using Parquet format.

    Implements TTL-based invalidation and cache metadata tracking.
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl_seconds: int = 3600,  # 1 hour default
    ):
        """
        Initialize data cache.

        Args:
            cache_dir: Directory to store cache files
            ttl_seconds: Time-to-live for cached data in seconds
        """
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds

        # Create cache directory if it doesn't exist
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Statistics tracking
        self._hits = 0
        self._misses = 0
        self._writes = 0

        logger.info(f"Initialized DataCache at {self.cache_dir} with TTL {ttl_seconds}s")

    def _get_cache_path(self, symbol: str, timeframe: str) -> Path:
        """
        Get the cache file path for a symbol/timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., '5m')

        Returns:
            Path to cache file
        """
        filename = f"{symbol.upper()}_{timeframe}.parquet"
        return self.cache_dir / filename

    def _get_metadata_path(self, symbol: str, timeframe: str) -> Path:
        """
        Get the metadata file path for a symbol/timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Path to metadata file
        """
        filename = f"{symbol.upper()}_{timeframe}.metadata.json"
        return self.cache_dir / filename

    def _load_metadata(self, symbol: str, timeframe: str) -> Optional[dict]:
        """
        Load metadata for cached data.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            Metadata dict or None if not found
        """
        metadata_path = self._get_metadata_path(symbol, timeframe)

        if not metadata_path.exists():
            return None

        try:
            with open(metadata_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading metadata for {symbol}/{timeframe}: {e}")
            return None

    def _save_metadata(self, symbol: str, timeframe: str, metadata: dict) -> None:
        """
        Save metadata for cached data.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            metadata: Metadata dict
        """
        metadata_path = self._get_metadata_path(symbol, timeframe)

        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f)
        except Exception as e:
            logger.warning(f"Error saving metadata for {symbol}/{timeframe}: {e}")

    def _is_cache_valid(self, symbol: str, timeframe: str) -> bool:
        """
        Check if cached data is still valid (within TTL).

        Args:
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            True if cache is valid, False otherwise
        """
        metadata = self._load_metadata(symbol, timeframe)

        if metadata is None:
            return False

        cache_path = self._get_cache_path(symbol, timeframe)
        if not cache_path.exists():
            return False

        try:
            last_update = datetime.fromisoformat(metadata["last_update"])
            age_seconds = (datetime.now() - last_update).total_seconds()

            is_valid = age_seconds < self.ttl_seconds
            return is_valid

        except Exception as e:
            logger.warning(f"Error checking cache validity for {symbol}/{timeframe}: {e}")
            return False

    def get(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[pd.DataFrame]:
        """
        Get cached data for a symbol/timeframe.

        Returns None if cache is missing or expired.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe

        Returns:
            DataFrame with cached data or None
        """
        if not self._is_cache_valid(symbol, timeframe):
            self._misses += 1
            return None

        cache_path = self._get_cache_path(symbol, timeframe)

        try:
            df = pd.read_parquet(cache_path)
            self._hits += 1

            logger.debug(f"Cache hit for {symbol}/{timeframe} ({len(df)} rows)")
            return df

        except Exception as e:
            logger.warning(f"Error reading cache for {symbol}/{timeframe}: {e}")
            self._misses += 1
            return None

    def put(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
    ) -> None:
        """
        Cache data for a symbol/timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            df: DataFrame with OHLCV data
        """
        if df.empty:
            logger.warning(f"Attempting to cache empty DataFrame for {symbol}/{timeframe}")
            return

        cache_path = self._get_cache_path(symbol, timeframe)

        try:
            # Save data as Parquet
            df.to_parquet(cache_path, index=False)

            # Save metadata
            metadata = {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "last_update": datetime.now().isoformat(),
                "row_count": len(df),
                "data_range": {
                    "start": df.iloc[0]["timestamp"].isoformat()
                    if "timestamp" in df.columns
                    else None,
                    "end": df.iloc[-1]["timestamp"].isoformat()
                    if "timestamp" in df.columns
                    else None,
                },
            }

            self._save_metadata(symbol, timeframe, metadata)
            self._writes += 1

            # Get file size
            file_size = cache_path.stat().st_size / 1024  # KB

            logger.info(
                f"Cached {len(df)} rows for {symbol}/{timeframe} "
                f"({file_size:.1f} KB)"
            )

        except Exception as e:
            logger.error(f"Error caching data for {symbol}/{timeframe}: {e}")

    def clear(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> None:
        """
        Clear cache for specific symbol/timeframe or all data.

        Args:
            symbol: Trading symbol (optional)
            timeframe: Timeframe (optional)
        """
        if symbol and timeframe:
            # Clear specific cache
            cache_path = self._get_cache_path(symbol, timeframe)
            metadata_path = self._get_metadata_path(symbol, timeframe)

            if cache_path.exists():
                cache_path.unlink()
            if metadata_path.exists():
                metadata_path.unlink()

            logger.info(f"Cleared cache for {symbol}/{timeframe}")

        elif symbol:
            # Clear all timeframes for a symbol
            for file in self.cache_dir.glob(f"{symbol.upper()}_*.parquet"):
                file.unlink()
            for file in self.cache_dir.glob(f"{symbol.upper()}_*.metadata.json"):
                file.unlink()

            logger.info(f"Cleared all cache for {symbol}")

        else:
            # Clear all cache
            for file in self.cache_dir.glob("*.parquet"):
                file.unlink()
            for file in self.cache_dir.glob("*.metadata.json"):
                file.unlink()

            logger.info("Cleared all cache")

    def stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        total_requests = self._hits + self._misses
        hit_rate = (
            (self._hits / total_requests * 100)
            if total_requests > 0
            else 0
        )

        # Calculate total cache size
        total_size = 0
        for file in self.cache_dir.glob("*.parquet"):
            total_size += file.stat().st_size

        # Count cached items
        cached_items = len(list(self.cache_dir.glob("*.parquet")))

        return {
            "hits": self._hits,
            "misses": self._misses,
            "total_requests": total_requests,
            "hit_rate": hit_rate,
            "writes": self._writes,
            "cached_items": cached_items,
            "total_size_mb": total_size / (1024 * 1024),
            "ttl_seconds": self.ttl_seconds,
        }

    def warm_cache(self) -> dict:
        """
        Warm the cache by loading metadata for all cached items.

        Returns:
            Dictionary with cache statistics after warming
        """
        logger.info("Warming cache...")

        cached_items = {}

        for metadata_file in self.cache_dir.glob("*.metadata.json"):
            try:
                with open(metadata_file, "r") as f:
                    metadata = json.load(f)

                symbol = metadata["symbol"]
                timeframe = metadata["timeframe"]

                if symbol not in cached_items:
                    cached_items[symbol] = []

                cached_items[symbol].append({
                    "timeframe": timeframe,
                    "rows": metadata["row_count"],
                    "last_update": metadata["last_update"],
                })

            except Exception as e:
                logger.warning(f"Error warming cache from {metadata_file}: {e}")

        logger.info(f"Cache warmed: {len(cached_items)} symbols, {sum(len(v) for v in cached_items.values())} timeframes")

        return cached_items
