"""Tests for Task 0.4: Abstract Data Provider Interface."""

import pytest
import pandas as pd
from datetime import datetime

from vibe.common.data import DataProvider
from vibe.common.models import Bar


def test_data_provider_abstract():
    """DataProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        DataProvider()


def test_data_provider_column_constants():
    """DataProvider has standard column name constants."""
    assert DataProvider.OPEN == "open"
    assert DataProvider.HIGH == "high"
    assert DataProvider.LOW == "low"
    assert DataProvider.CLOSE == "close"
    assert DataProvider.VOLUME == "volume"


def test_data_provider_has_required_methods():
    """DataProvider has all required abstract methods."""
    required_methods = [
        "get_bars",
        "get_current_price",
        "get_bar",
    ]

    for method_name in required_methods:
        assert hasattr(DataProvider, method_name)


class ConcreteDataProvider(DataProvider):
    """Concrete implementation of DataProvider for testing."""

    async def get_bars(
        self,
        symbol,
        timeframe="1m",
        limit=None,
        start_time=None,
        end_time=None,
    ):
        """Dummy implementation returning sample data."""
        data = {
            "timestamp": pd.date_range(start="2024-01-15", periods=5, freq="1h"),
            self.OPEN: [100.0, 101.0, 102.0, 101.5, 103.0],
            self.HIGH: [101.0, 102.0, 103.0, 102.5, 104.0],
            self.LOW: [99.0, 100.0, 101.0, 100.5, 102.0],
            self.CLOSE: [100.5, 101.5, 102.5, 102.0, 103.5],
            self.VOLUME: [1000000, 1100000, 900000, 1200000, 1050000],
        }
        return pd.DataFrame(data)

    async def get_current_price(self, symbol):
        """Dummy implementation."""
        return 100.0

    async def get_bar(self, symbol, timeframe="1m"):
        """Dummy implementation."""
        return Bar(
            timestamp=datetime.now(),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000000,
        )


def test_data_provider_concrete_implementation():
    """DataProvider can be subclassed and instantiated."""
    provider = ConcreteDataProvider()
    assert provider is not None


@pytest.mark.asyncio
async def test_data_provider_get_bars_returns_dataframe():
    """get_bars returns DataFrame with standard columns."""
    provider = ConcreteDataProvider()
    df = await provider.get_bars("AAPL")

    assert isinstance(df, pd.DataFrame)
    assert "open" in df.columns
    assert "high" in df.columns
    assert "low" in df.columns
    assert "close" in df.columns
    assert "volume" in df.columns


@pytest.mark.asyncio
async def test_data_provider_get_current_price_returns_float():
    """get_current_price returns a float."""
    provider = ConcreteDataProvider()
    price = await provider.get_current_price("AAPL")

    assert isinstance(price, (int, float))
    assert price > 0


@pytest.mark.asyncio
async def test_data_provider_get_bar_returns_bar_object():
    """get_bar returns a Bar object."""
    provider = ConcreteDataProvider()
    bar = await provider.get_bar("AAPL")

    assert isinstance(bar, Bar)
    assert bar.open > 0
    assert bar.close > 0
