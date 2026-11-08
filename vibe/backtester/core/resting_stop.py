"""1-minute simulation of a resting ORB stop-market entry.

Supports ``EntryFillPolicy.RESTING_STOP``. A resting stop is triggered the
moment price reaches its level, so the simulation must look at bars finer than
the strategy timeframe and may only use what is known at that moment: the
opening-range levels, the clock, and the minute being traded.

Everything here is pure array work over one session's 1-minute bars; the
engine owns orders, sizing, and the portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from vibe.common.models.bar import Bar

EXCHANGE_TZ = "America/New_York"
_MINUTE_NS = 60 * 1_000_000_000


@dataclass(frozen=True)
class Trigger:
    """The first 1-minute bar that reached a resting entry level."""

    index: int
    side: Optional[str]
    """``"buy"`` or ``"sell"``; ``None`` when the minute is ambiguous."""
    base_price: float
    """Fill before slippage: the trigger, or the minute's open if it opened
    beyond the trigger."""

    @property
    def ambiguous(self) -> bool:
        return self.side is None


class SessionMinutes:
    """One session's 1-minute bars as arrays, searchable by absolute time."""

    def __init__(self, frame: pd.DataFrame) -> None:
        index = frame.index
        if index.tz is None:
            index = index.tz_localize(EXCHANGE_TZ)
        index = index.as_unit("ns")
        self.index = index
        self.ns = index.asi8
        self.open = frame["open"].to_numpy(dtype=float)
        self.high = frame["high"].to_numpy(dtype=float)
        self.low = frame["low"].to_numpy(dtype=float)
        self.close = frame["close"].to_numpy(dtype=float)
        self.volume = frame["volume"].to_numpy(dtype=float)

    def __len__(self) -> int:
        return len(self.ns)

    @property
    def minute_resolution(self) -> bool:
        """True when these really are 1-minute bars.

        A session whose source rows are coarser (say, 5-minute bars) cannot
        locate a stop trigger within the bar, so it must be skipped rather
        than priced as if it could.
        """
        if len(self.ns) < 2:
            return False
        if np.any(self.ns % _MINUTE_NS):
            return False
        return int(np.diff(self.ns).min()) == _MINUTE_NS

    def span(self, start: pd.Timestamp, end: pd.Timestamp) -> tuple[int, int]:
        """Row positions ``[lo, hi)`` of bars stamped in ``[start, end)``."""
        lo = int(np.searchsorted(self.ns, _ns(start), side="left"))
        hi = int(np.searchsorted(self.ns, _ns(end), side="left"))
        return lo, hi

    def opening_range(
        self, start: pd.Timestamp, end: pd.Timestamp
    ) -> Optional[tuple[float, float]]:
        """(high, low) of the bars in ``[start, end)``, or None if there are none."""
        lo, hi = self.span(start, end)
        if hi <= lo:
            return None
        return float(self.high[lo:hi].max()), float(self.low[lo:hi].min())

    def first_trigger(
        self,
        lo: int,
        hi: int,
        *,
        long_level: Optional[float],
        short_level: Optional[float],
        long_stop: Optional[float] = None,
        short_stop: Optional[float] = None,
    ) -> Optional[Trigger]:
        """The first minute in ``[lo, hi)`` that reached a resting level.

        ``long_level``/``short_level`` are the buy-stop and sell-stop prices,
        or None when that side is not resting. ``long_stop``/``short_stop``
        are the protective stops each entry would carry.

        A minute is reported as ambiguous when it reaches both entry levels,
        or reaches an entry and that entry's own initial stop: OHLC cannot say
        which happened first, and guessing either way flatters or penalises
        the result by assumption.
        """
        if hi <= lo or (long_level is None and short_level is None):
            return None
        high = self.high[lo:hi]
        low = self.low[lo:hi]
        long_hit = (
            high >= long_level if long_level is not None else np.zeros(len(high), bool)
        )
        short_hit = (
            low <= short_level if short_level is not None else np.zeros(len(low), bool)
        )
        hits = np.flatnonzero(long_hit | short_hit)
        if hits.size == 0:
            return None
        k = int(hits[0])
        i = lo + k
        both_sides = long_hit[k] and short_hit[k]
        long_own_stop = (
            long_hit[k] and long_stop is not None and low[k] <= long_stop
        )
        short_own_stop = (
            short_hit[k] and short_stop is not None and high[k] >= short_stop
        )
        if both_sides or long_own_stop or short_own_stop:
            return Trigger(index=i, side=None, base_price=float("nan"))
        if long_hit[k]:
            return Trigger(
                index=i, side="buy", base_price=max(long_level, float(self.open[i]))
            )
        return Trigger(
            index=i, side="sell", base_price=min(short_level, float(self.open[i]))
        )

    def bar(self, i: int, *, close: Optional[float] = None) -> Bar:
        """Row ``i`` as a ``Bar``; ``close`` overrides the close.

        The fill paths price a market order off ``close``, so passing the
        trigger fill price there makes them fill at it without knowing about
        the policy -- the same device ``_next_session_bar`` uses.
        """
        return Bar(
            timestamp=self.index[i].to_pydatetime(),
            open=float(self.open[i]),
            high=float(self.high[i]),
            low=float(self.low[i]),
            close=float(self.close[i]) if close is None else float(close),
            volume=float(self.volume[i]),
        )


def split_sessions(df_1m: pd.DataFrame) -> dict[date, SessionMinutes]:
    """Group source bars by exchange-local session date."""
    if df_1m.empty:
        return {}
    index = df_1m.index
    local = index.tz_convert(EXCHANGE_TZ) if index.tz is not None else index
    return {
        session: SessionMinutes(group)
        for session, group in df_1m.groupby(local.date, sort=True)
    }


def session_time(session: date, hhmm: str, tz) -> pd.Timestamp:
    """``hhmm`` on ``session`` in exchange time, expressed in ``tz``."""
    hour, minute = map(int, hhmm.split(":"))
    ts = pd.Timestamp(
        year=session.year, month=session.month, day=session.day,
        hour=hour, minute=minute, tz=EXCHANGE_TZ,
    )
    if tz is None:
        return ts.tz_localize(None)
    return ts.tz_convert(tz)


def _ns(ts: pd.Timestamp) -> int:
    ts = pd.Timestamp(ts)
    if ts.tz is None:
        ts = ts.tz_localize(EXCHANGE_TZ)
    return int(ts.value)
