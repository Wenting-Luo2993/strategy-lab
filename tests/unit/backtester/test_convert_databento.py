from datetime import datetime

import pandas as pd
import pytz

from scripts.convert_databento import _filter_exchange_sessions


def _bars(day: str, end: str) -> pd.DataFrame:
    index = pd.date_range(
        f"{day} 09:30",
        f"{day} {end}",
        freq="1min",
        tz="America/New_York",
    )
    return pd.DataFrame(
        {
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
        },
        index=index,
    )


def test_filters_early_close_at_exchange_boundary():
    bars = _bars("2024-07-03", "15:59")
    filtered = _filter_exchange_sessions(bars)

    assert len(filtered) == 210
    assert filtered.index[-1] == pd.Timestamp(
        datetime(2024, 7, 3, 12, 59),
        tz=pytz.timezone("America/New_York"),
    )


def test_keeps_complete_regular_session():
    bars = _bars("2024-07-02", "15:59")
    filtered = _filter_exchange_sessions(bars)

    assert len(filtered) == 390
    assert filtered.index.equals(bars.index)


def test_removes_exchange_holiday_bars():
    bars = _bars("2024-07-04", "15:59")

    assert _filter_exchange_sessions(bars).empty
