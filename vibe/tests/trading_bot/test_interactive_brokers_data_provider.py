from datetime import datetime, timezone

import pytest

from vibe.trading_bot.brokers.base import BrokerQuote
from vibe.trading_bot.data.providers.interactive_brokers import InteractiveBrokersDataProvider


class FakeBroker:
    market_data_type = 1

    def __init__(self):
        self.ib = self
        self.connected = True
        self.connect_calls = 0
        self.disconnect_calls = 0

    def isConnected(self):
        return self.connected

    async def connect(self):
        self.connect_calls += 1
        self.connected = True
        return True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

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
    assert bar["timestamp"].tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_ib_data_provider_shared_mode_does_not_disconnect_broker():
    broker = FakeBroker()
    provider = InteractiveBrokersDataProvider(broker, owns_broker_lifecycle=False)

    assert await provider.connect() is True
    await provider.disconnect()
    assert provider.connected is False
    assert broker.isConnected() is True
    assert broker.connect_calls == broker.disconnect_calls == 0


@pytest.mark.asyncio
async def test_ib_data_provider_standalone_mode_disconnects_broker():
    broker = FakeBroker()
    provider = InteractiveBrokersDataProvider(broker)

    assert await provider.connect() is True
    await provider.disconnect()
    assert provider.connected is False
    assert broker.isConnected() is False
    assert broker.connect_calls == broker.disconnect_calls == 1
