"""Unit tests for Interactive Brokers API error handling."""

import pytest

from vibe.trading_bot.brokers.interactive_brokers import (
    IBOperatorActionRequired,
    InteractiveBrokersAPI,
)


class FakeErrorEvent:
    def __init__(self):
        self.handler = None

    def __iadd__(self, handler):
        self.handler = handler
        return self


class FakeIB:
    def __init__(self):
        self.errorEvent = FakeErrorEvent()
        self.disconnected = False

    def isConnected(self):
        return False

    async def connectAsync(self, *args, **kwargs):
        self.errorEvent.handler(
            -1,
            10141,
            "Paper trading disclaimer must first be accepted for API connection.",
        )
        raise TimeoutError()

    def disconnect(self):
        self.disconnected = True


class FakeUnknownErrorIB:
    instances = []

    def __init__(self):
        self.errorEvent = FakeErrorEvent()
        self.attempts = 0
        self.disconnected = False
        FakeUnknownErrorIB.instances.append(self)

    def isConnected(self):
        return False

    async def connectAsync(self, *args, **kwargs):
        self.attempts += 1
        raise TimeoutError("transient connect failure")

    def disconnect(self):
        self.disconnected = True


@pytest.mark.asyncio
async def test_connect_raises_operator_action_required_for_paper_disclaimer(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    monkeypatch.setattr(ib_module, "IB", FakeIB)

    api = InteractiveBrokersAPI()

    with pytest.raises(IBOperatorActionRequired, match="paper trading disclaimer"):
        await api.connect()

    assert api.ib.disconnected is False


@pytest.mark.asyncio
async def test_connect_unknown_error_retries_three_times(monkeypatch):
    import vibe.trading_bot.brokers.interactive_brokers as ib_module

    FakeUnknownErrorIB.instances = []
    monkeypatch.setattr(ib_module, "IB", FakeUnknownErrorIB)

    api = InteractiveBrokersAPI(connect_retry_delay_seconds=0)

    with pytest.raises(ib_module.IBConnectionFailed, match="after 3 attempts"):
        await api.connect()

    assert api.ib.attempts == 3