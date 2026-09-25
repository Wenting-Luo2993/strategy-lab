import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.data.paths import available_symbols, resolve_market_data_dir
from vibe.common.data.base import DataProvider
from vibe.common.models.bar import Bar
from vibe.research_pipeline.holdout import HoldoutLock, UnlockToken, load_lock

logger = logging.getLogger(__name__)


class ParquetLoader(DataProvider):
    """
    Implements DataProvider for backtesting against local Parquet files.

    All symbols are loaded into memory at init (eager load). Subsequent
    get_bars / get_current_price / get_bar calls are pure in-memory slices.

    Parquet files are produced by scripts/convert_databento.py.
    Location resolves via vibe.backtester.data.paths (BACKTEST__DATA_DIR, the
    repository default, then the main worktree); pass data_dir to override.

    Final-holdout guard
    -------------------
    This class is the single choke point through which every backtest reads
    market data, which makes it the only place a holdout boundary can be
    enforced rather than merely asserted. Requests are checked against
    ``config/final_holdout.yaml``:

    * An **explicit** ``end_time`` after ``dev_end`` raises ``HoldoutViolation``
      unless an ``UnlockToken`` was supplied. The caller stated an intent to
      read reserved data, so the refusal is loud.
    * An **unbounded** request (``end_time=None``) is clamped to ``dev_end``
      and logged. The caller expressed no intent, and clamping is the only
      option that cannot leak: returning everything would contaminate silently,
      while raising would break every legitimate "load all development data"
      call. The clamp is logged rather than silent because it does change what
      the caller receives.

    Pass ``enforce_holdout=False`` only to read the holdout deliberately --
    and prefer ``holdout_token``, which records *why* in the access log.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        symbols: list[str] | None = None,
        *,
        enforce_holdout: bool = True,
        holdout_token: Optional[UnlockToken] = None,
    ) -> None:
        resolved = resolve_market_data_dir(data_dir)
        self.data_dir = resolved
        requested = list(symbols or [])
        self.enforce_holdout = enforce_holdout
        self.holdout_token = holdout_token
        self._lock: Optional[HoldoutLock] = None
        if enforce_holdout:
            try:
                self._lock = load_lock()
            except FileNotFoundError:
                # No committed lock yet. Enforcing an undeclared boundary would
                # invent one, so fall through -- but say so, because a silently
                # unguarded loader is exactly the state this guard exists to
                # make impossible to be in unknowingly.
                logger.warning(
                    "No config/final_holdout.yaml found; the final-holdout "
                    "guard is inactive and backtests may read reserved data."
                )

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

    def _guard(
        self,
        symbol: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> Optional[pd.Timestamp]:
        """Apply the holdout boundary. Returns a clamp bound, or None.

        Returning the clamp rather than applying it here keeps the slicing in
        one place in ``get_bars``.
        """
        lock = self._lock
        if lock is None or not lock.covers(symbol):
            return None

        dev_end = lock.dev_end
        if end_time is not None:
            if end_time.date() <= dev_end:
                return None
            lock.assert_within_dev(
                symbol=symbol,
                start=(start_time.date() if start_time else lock.oos_start),
                end=end_time.date(),
                token=self.holdout_token,
            )
            # A token was supplied and the access is now logged; honour it.
            return None

        # Unbounded: clamp to the last development session.
        logger.info(
            "Clamping unbounded %s request to dev_end %s; sessions after that "
            "are reserved for the final out-of-sample evaluation.",
            symbol,
            dev_end,
        )
        return pd.Timestamp(dev_end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        df = self._data[symbol]
        clamp = self._guard(symbol, start_time, end_time)
        if start_time is not None:
            df = df[df.index >= start_time]
        if end_time is not None:
            df = df[df.index <= end_time]
        if clamp is not None:
            bound = clamp.tz_localize(df.index.tz) if df.index.tz is not None else clamp
            df = df[df.index <= bound]
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
        """Return the in-memory DataFrame, clamped to ``dev_end``.

        Named "full", but the holdout clamp still applies. A method that
        bypassed the guard would be an unlocked back door beside a locked
        front one, and callers would reach for it precisely when the guard
        was inconvenient.
        """
        df = self._data[symbol]
        clamp = self._guard(symbol, None, None)
        if clamp is not None:
            bound = clamp.tz_localize(df.index.tz) if df.index.tz is not None else clamp
            df = df[df.index <= bound]
        return df
