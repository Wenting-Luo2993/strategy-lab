from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.data.paths import available_symbols, resolve_market_data_dir
from vibe.common.data.base import DataProvider
from vibe.common.models.bar import Bar


class ParquetLoader(DataProvider):
    """
    Implements DataProvider for backtesting against local Parquet files.

    All symbols are loaded into memory at init (eager load). Subsequent
    get_bars / get_current_price / get_bar calls are pure in-memory slices.

    Parquet files are produced by scripts/convert_databento.py.
    Location resolves via vibe.backtester.data.paths (BACKTEST__DATA_DIR, the
    repository default, then the main worktree); pass data_dir to override.
    """

    def __init__(
        self, data_dir: Path | str | None = None, symbols: list[str] | None = None
    ) -> None:
        resolved = resolve_market_data_dir(data_dir)
        self.data_dir = resolved
        requested = list(symbols or [])

        missing = [
            sym for sym in requested if not (resolved / f"{sym}.parquet").exists()
        ]
        if missing:
            present = available_symbols(resolved)
            raise FileNotFoundError(
                f"No parquet data for {', '.join(missing)} in {resolved}. "
                f"Available: {', '.join(present) if present else '(none)'}."
            )

        self._data: dict[str, pd.DataFrame] = {
            sym: pd.read_parquet(resolved / f"{sym}.parquet")
            for sym in requested
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
