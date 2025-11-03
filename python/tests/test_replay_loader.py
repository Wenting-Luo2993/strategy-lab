import pandas as pd
from datetime import datetime, timedelta, time
import pytz

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


def test_replay_initial_excludes_last_day(monkeypatch, tmp_path):
    df = make_two_day_intraday(rows_per_day=8)
    # Write to parquet to simulate cache file naming SYMBOL_5m.parquet
    cache_dir = tmp_path
    path = cache_dir / "TEST_5m.parquet"
    df.to_parquet(path)
    loader = DataReplayCacheDataLoader(market_open=time(9,30), cache_dir=str(cache_dir))
    out = loader.fetch("TEST", "5m")
    # Should contain all of day1 only (no rows from last day revealed yet)
    assert out.index.date.max() == datetime(2024,10,1).date()


def test_replay_progressive_advance(tmp_path):
    df = make_two_day_intraday(rows_per_day=6)
    cache_dir = tmp_path
    df.to_parquet(cache_dir/"TEST_5m.parquet")
    loader = DataReplayCacheDataLoader(market_open=time(9,30), cache_dir=str(cache_dir), reveal_increment=2)
    _ = loader.fetch("TEST","5m")  # init state
    assert loader.replay_progress("TEST","5m") == 0.0  # initial
    loader.advance(n=1)  # reveal 2 rows
    prog1 = loader.replay_progress("TEST","5m")
    assert 0 < prog1 < 1
    loader.advance(n=10)  # overshoot to full
    assert loader.replay_progress("TEST","5m") == 1.0


def test_start_offset_reveals_premarket(tmp_path):
    df = make_two_day_intraday(rows_per_day=12)
    cache_dir = tmp_path
    df.to_parquet(cache_dir/"TEST_5m.parquet")
    loader = DataReplayCacheDataLoader(market_open=time(9,30), cache_dir=str(cache_dir), start_offset_minutes=10)
    _ = loader.fetch("TEST","5m")
    # Dataset begins exactly at open; earlier offset gives zero initial reveal
    assert loader.replay_progress("TEST","5m") == 0.0
