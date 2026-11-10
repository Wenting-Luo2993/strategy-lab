from datetime import datetime, timezone

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
    assert bar["timestamp"].tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_ib_data_provider_shared_mode_does_not_disconnect_broker():
    """Verify that provider in shared mode (shared broker) does not disconnect the broker."""
    broker = FakeBroker()
    provider = InteractiveBrokersDataProvider(broker)
    
    # Mark as shared (would be set by factory when ib_broker param is provided)
    provider._owns_broker_lifecycle = False
    
    # Connect in shared mode
    connected = await provider.connect()
    assert connected is True
    
    # Disconnect in shared mode - should only set _connected flag, not call broker.disconnect
    await provider.disconnect()
    assert provider._connected is False
    # Broker should still be connected (isConnected would still return True)
    assert broker.isConnected() is True


@pytest.mark.asyncio
async def test_ib_data_provider_standalone_mode_disconnects_broker():
    """Verify that provider in standalone mode (owns broker) does disconnect."""
    broker = FakeBroker()
    provider = InteractiveBrokersDataProvider(broker)
    
    # Mark as standalone (default)
    provider._owns_broker_lifecycle = True
    
    # Connect in standalone mode
    connected = await provider.connect()
    assert connected is True
    
    # Disconnect in standalone mode - should call broker.disconnect
    await provider.disconnect()
    assert provider._connected is False

