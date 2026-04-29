from datetime import datetime, time
from zoneinfo import ZoneInfo

from vibe.common.clock.base import Clock


class SimulatedClock(Clock):
    """
    Backtester clock driven by bar timestamps.
    Engine calls set_time(ts) before each bar; now() returns it.
    is_market_open() checks 9:30–16:00 ET.
    """

    _MARKET_OPEN = time(9, 30)
    _MARKET_CLOSE = time(16, 0)
    _MARKET_TZ = ZoneInfo("America/New_York")

    def __init__(self) -> None:
        self._current: datetime | None = None

    def set_time(self, ts: datetime) -> None:
        self._current = ts

    def now(self) -> datetime:
        if self._current is None:
            raise RuntimeError("SimulatedClock has not been set — call set_time() first")
        return self._current

    def is_market_open(self) -> bool:
        if self._current is None:
            return False
        local = self._current.astimezone(self._MARKET_TZ)
        return self._MARKET_OPEN <= local.time() < self._MARKET_CLOSE
