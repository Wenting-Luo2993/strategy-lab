import pytest
import asyncio
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

PARQUET_DIR = Path("vibe/data/parquet")
pytestmark = pytest.mark.skipif(
    not (PARQUET_DIR / "QQQ.parquet").exists(),
    reason="Parquet data not available"
)


@pytest.fixture
def loader():
    from vibe.backtester.data.parquet_loader import ParquetLoader
    return ParquetLoader(PARQUET_DIR, ["QQQ"])


def test_loads_data_at_init(loader):
    assert "QQQ" in loader._data
    df = loader._data["QQQ"]
    assert set(df.columns) == {"open", "high", "low", "close", "volume"}


def test_get_bars_full_range(loader):
    df = asyncio.run(loader.get_bars("QQQ"))
    assert len(df) > 0
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_get_bars_with_time_filter(loader):
    start = datetime(2024, 1, 2, 9, 30, tzinfo=ET)
    end = datetime(2024, 1, 31, 16, 0, tzinfo=ET)
    df = asyncio.run(loader.get_bars("QQQ", start_time=start, end_time=end))
    assert len(df) > 0
    assert df.index[0] >= start
    assert df.index[-1] <= end


def test_get_bars_with_limit(loader):
    df = asyncio.run(loader.get_bars("QQQ", limit=50))
    assert len(df) == 50


def test_get_current_price(loader):
    price = asyncio.run(loader.get_current_price("QQQ"))
    assert isinstance(price, float)
    assert price > 0


def test_get_bar(loader):
    from vibe.common.models.bar import Bar
    bar = asyncio.run(loader.get_bar("QQQ"))
    assert isinstance(bar, Bar)
    assert bar.close > 0


def test_unknown_symbol_raises(loader):
    with pytest.raises(KeyError):
        asyncio.run(loader.get_bars("UNKNOWN"))
