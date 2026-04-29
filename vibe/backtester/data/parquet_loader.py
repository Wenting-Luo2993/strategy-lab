from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.common.data.base import DataProvider
from vibe.common.models.bar import Bar


class ParquetLoader(DataProvider):
    """
    Implements DataProvider for backtesting against local Parquet files.

    All symbols are loaded into memory at init (eager load). Subsequent
    get_bars / get_current_price / get_bar calls are pure in-memory slices.

    Parquet files are produced by scripts/convert_databento.py.
    Path configured via BACKTEST__DATA_DIR in .env.
    """

    def __init__(self, data_dir: Path, symbols: list[str]) -> None:
        self._data: dict[str, pd.DataFrame] = {
            sym: pd.read_parquet(data_dir / f"{sym}.parquet")
            for sym in symbols
        }

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        df = self._data[symbol]
        if start_time is not None:
            df = df[df.index >= start_time]
        if end_time is not None:
            df = df[df.index <= end_time]
        if limit is not None:
            df = df.tail(limit)
        return df

    async def get_current_price(self, symbol: str) -> float:
        return float(self._data[symbol]["close"].iloc[-1])

    async def get_bar(self, symbol: str, timeframe: str = "1m") -> Optional[Bar]:
        row = self._data[symbol].iloc[-1]
        return Bar(
            timestamp=row.name.to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )

    def get_full_df(self, symbol: str) -> pd.DataFrame:
        """Return the full in-memory DataFrame (used by BacktestEngine for batch processing)."""
        return self._data[symbol]
