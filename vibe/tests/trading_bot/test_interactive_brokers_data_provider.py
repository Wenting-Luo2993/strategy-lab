from datetime import datetime

import pytest

from vibe.trading_bot.brokers.base import BrokerQuote
from vibe.trading_bot.data.providers.interactive_brokers import InteractiveBrokersDataProvider


class FakeBroker:
    market_data_type = 1

    def __init__(self):
        self.ib = self

    def isConnected(self):
        return True

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def get_market_data(self, symbol: str):
        return BrokerQuote(
            symbol=symbol,
            bid=720.0,
            ask=720.1,
            last=720.05,
            market_price=720.05,
            timestamp=datetime(2026, 7, 14, 10, 0),
        )


@pytest.mark.asyncio
async def test_ib_snapshot_bar_uses_positive_synthetic_volume():
    provider = InteractiveBrokersDataProvider(FakeBroker())

    bar = await provider.get_latest_bar("QQQ")

    assert bar["symbol"] == "QQQ"
    assert bar["close"] == 720.05
    assert bar["volume"] > 0
