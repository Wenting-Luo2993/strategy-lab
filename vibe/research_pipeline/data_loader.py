"""Research-policy wrapper around the backtester's neutral parquet loader."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from vibe.backtester.data.parquet_loader import ParquetLoader
from vibe.research_pipeline.holdout import HoldoutLock, UnlockToken, load_lock
from vibe.research_pipeline.methodology import ClaimedFinalHoldoutAuthorization

logger = logging.getLogger(__name__)


class GuardedParquetLoader(ParquetLoader):
    def __init__(
        self,
        data_dir: Path | str | None = None,
        symbols: list[str] | None = None,
        *,
        enforce_holdout: bool = True,
        holdout_token: Optional[
            UnlockToken | ClaimedFinalHoldoutAuthorization
        ] = None,
    ) -> None:
        super().__init__(data_dir, symbols)
        self.holdout_token = holdout_token
        self._lock: HoldoutLock | None = load_lock() if enforce_holdout else None

    def _guard(
        self,
        symbol: str,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> pd.Timestamp | None:
        lock = self._lock
        if lock is None or not lock.covers(symbol):
            return None
        if end_time is not None:
            if end_time.date() <= lock.dev_end:
                return None
            lock.assert_within_dev(
                symbol=symbol,
                start=start_time.date() if start_time else lock.oos_start,
                end=end_time.date(),
                token=self._unlock_token(),
            )
            return None
        logger.info("Clamping unbounded %s research request to %s", symbol, lock.dev_end)
        return (
            pd.Timestamp(lock.dev_end)
            + pd.Timedelta(days=1)
            - pd.Timedelta(nanoseconds=1)
        )

    def _unlock_token(self) -> UnlockToken | None:
        token = self.holdout_token
        if isinstance(token, ClaimedFinalHoldoutAuthorization):
            return UnlockToken(
                reason=token.reason,
                requested_by=token.requested_by,
                run_id=token.claim_id,
                log_access=False,
            )
        return token

    async def get_bars(self, symbol: str, timeframe: str = "1m", limit=None,
                       start_time=None, end_time=None):
        clamp = self._guard(symbol, start_time, end_time)
        frame = await super().get_bars(
            symbol, timeframe, limit, start_time, end_time
        )
        if clamp is not None:
            bound = (
                clamp.tz_localize(frame.index.tz)
                if frame.index.tz is not None else clamp
            )
            frame = frame[frame.index <= bound]
        return frame

    def get_full_df(self, symbol: str) -> pd.DataFrame:
        clamp = self._guard(symbol, None, None)
        frame = super().get_full_df(symbol)
        if clamp is None:
            return frame
        bound = (
            clamp.tz_localize(frame.index.tz)
            if frame.index.tz is not None else clamp
        )
        return frame[frame.index <= bound]
