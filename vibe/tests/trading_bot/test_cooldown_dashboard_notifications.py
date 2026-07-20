"""Tests for cooldown dashboard publication notifications."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vibe.trading_bot.core.phases import cooldown as cooldown_module
from vibe.trading_bot.core.phases.cooldown import CooldownPhaseManager


class FakeMarketScheduler:
    def now(self):
        return datetime(2026, 7, 20, 16, 5, tzinfo=timezone.utc)


class FakePublisher:
    def __init__(self, summary):
        self.summary = summary

    async def flush_pending(self, timeout_seconds, max_batches):
        assert timeout_seconds == 30.0
        assert max_batches == 5
        return SimpleNamespace(claimed=1, published=0, failed=1, dead_lettered=0)

    def publish_cooldown_summary(self):
        return self.summary


def _manager(summary):
    orchestrator = SimpleNamespace(
        _testing_mode=True,
        config=SimpleNamespace(
            notifications=SimpleNamespace(
                discord_webhook_url="https://discord.example/webhook",
                notify_on_error=True,
            )
        ),
        market_scheduler=FakeMarketScheduler(),
        remote_data_publisher=FakePublisher(summary),
    )
    return CooldownPhaseManager(orchestrator)


@pytest.mark.asyncio
async def test_cooldown_sends_dashboard_publish_alert_for_unresolved_rows(monkeypatch):
    sent_payloads = []

    class FakeNotifier:
        async def send_system_alert(self, payload):
            sent_payloads.append(payload)
            return True

    @asynccontextmanager
    async def fake_discord_context(webhook_url):
        assert webhook_url == "https://discord.example/webhook"
        yield FakeNotifier()

    monkeypatch.setattr(cooldown_module, "discord_notification_context", fake_discord_context)
    manager = _manager({"published": 3, "failed": 1, "pending": 2, "dead_letter": 0})

    await manager._flush_dashboard_publisher()

    assert len(sent_payloads) == 1
    payload = sent_payloads[0]
    assert payload.event_type == "SYSTEM_WARNING"
    assert payload.severity == "warning"
    assert payload.component == "RemoteDataPublisher"
    assert payload.details["failed"] == 1
    assert payload.details["pending"] == 2
    assert payload.details["unresolved_total"] == 3


@pytest.mark.asyncio
async def test_cooldown_does_not_send_dashboard_publish_alert_when_clear(monkeypatch):
    sent_payloads = []

    @asynccontextmanager
    async def fake_discord_context(webhook_url):
        raise AssertionError("Discord should not be called when no unresolved rows remain")
        yield

    monkeypatch.setattr(cooldown_module, "discord_notification_context", fake_discord_context)
    manager = _manager({"published": 3, "failed": 0, "pending": 0, "dead_letter": 0})

    await manager._flush_dashboard_publisher()

    assert sent_payloads == []