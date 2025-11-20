import pandas as pd
from datetime import datetime, timedelta, time
import pytz
import pytest

from src.data.replay_cache import DataReplayCacheDataLoader


def make_two_day_intraday(rows_per_day=10):
    tz = pytz.UTC
    day1 = datetime(2024,10,1,9,30)
    day2 = datetime(2024,10,2,9,30)
    data = []
    for d in [day1, day2]:
        for i in range(rows_per_day):
            ts = d + timedelta(minutes=5*i)
            price = 100 + i if d==day1 else 200 + i
            data.append((ts, price, price+0.5, price-0.5, price+0.1, 1000))
    df = pd.DataFrame(data, columns=["timestamp","open","high","low","close","volume"]).set_index("timestamp")
    df.index = df.index.tz_localize(pytz.UTC)
    return df


def test_replay_initial_includes_opening_bar(tmp_path):
    """Initial fetch should now include ONLY the opening bar of the replay (final) day.

    Previously the loader returned only history. New semantics always reveal the opening bar.
    """
    rows_per_day = 8
    df = make_two_day_intraday(rows_per_day=rows_per_day)
    cache_dir = tmp_path
    (cache_dir / "TEST_5m.parquet").write_bytes(b"")  # ensure dir exists
    df.to_parquet(cache_dir / "TEST_5m.parquet")
    loader = DataReplayCacheDataLoader(market_open=time(9,30), cache_dir=str(cache_dir))
    out = loader.fetch("TEST", "5m")
    # Last date should now be the final day, but only one bar from it revealed
    assert out.index.date.max() == datetime(2024,10,2).date()
    # Progress should be exactly 1 / rows_in_replay_day
    assert loader.replay_progress("TEST","5m") == pytest.approx(1/rows_per_day)


def test_replay_progressive_advance(tmp_path):
    rows_per_day = 6
    df = make_two_day_intraday(rows_per_day=rows_per_day)
    cache_dir = tmp_path
    df.to_parquet(cache_dir/"TEST_5m.parquet")
    loader = DataReplayCacheDataLoader(market_open=time(9,30), cache_dir=str(cache_dir), reveal_increment=2)
    _ = loader.fetch("TEST","5m")  # init state
    # Initial progress should reflect the single opening bar
    assert loader.replay_progress("TEST","5m") == pytest.approx(1/rows_per_day)
    loader.advance(n=1)  # reveal 2 additional rows (total 3 of 6)
    prog1 = loader.replay_progress("TEST","5m")
    assert prog1 == pytest.approx(3/rows_per_day)
    loader.advance(n=10)  # overshoot to full
    assert loader.replay_progress("TEST","5m") == 1.0


def test_start_offset_reveals_open_when_no_premarket(tmp_path):
    """If offset requests premarket but dataset begins at market open, we still reveal the opening bar only."""
    rows_per_day = 12
    df = make_two_day_intraday(rows_per_day=rows_per_day)
    cache_dir = tmp_path
    df.to_parquet(cache_dir/"TEST_5m.parquet")
    loader = DataReplayCacheDataLoader(market_open=time(9,30), cache_dir=str(cache_dir), start_offset_minutes=10)
    _ = loader.fetch("TEST","5m")
    assert loader.replay_progress("TEST","5m") == pytest.approx(1/rows_per_day)
