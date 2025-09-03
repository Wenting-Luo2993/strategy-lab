# src/data/cache.py
import os
import pandas as pd
from pathlib import Path
from typing import Optional
from src.data.base import DataLoader

class CacheDataLoader(DataLoader):
    def __init__(self, wrapped_loader: DataLoader, cache_dir: str = "data_cache"):
        self.wrapped_loader = wrapped_loader
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(
        self, symbol: str, timeframe: str, start: Optional[str], end: Optional[str]
    ) -> Path:
        # Replace invalid characters with underscore
        clean_symbol = symbol.replace(":", "_").replace("/", "_")
        name = f"{clean_symbol}_{timeframe}_{start or 'start'}_{end or 'end'}.parquet"
        return self.cache_dir / name

    def fetch(self, symbol: str, timeframe: str, start: Optional[str] = None, end: Optional[str] = None
    ) -> pd.DataFrame:
        cache_file = self._cache_path(symbol, timeframe, start, end)

        # 1. Try cache
        if cache_file.exists():
            print(f"[Cache] Loaded from {cache_file}")
            return pd.read_parquet(cache_file)

        # 2. Fetch via wrapped loader
        print(f"[Cache] Miss → fetching {symbol} {timeframe} from API")
        df = self.wrapped_loader.fetch(symbol, start, end)

        # 3. Store in cache
        df.to_parquet(cache_file)
        print(f"[Cache] Saved to {cache_file}")
        return df