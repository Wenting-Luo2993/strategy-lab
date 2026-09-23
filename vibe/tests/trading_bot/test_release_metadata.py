"""Tests for immutable release identity on remote operational records."""

from datetime import datetime

from vibe.trading_bot import release_metadata as release_metadata_module
from vibe.trading_bot.storage.operational_metrics import (
    OperationalMetric,
    SupabaseRestMetricsSink,
)


def test_release_metadata_uses_environment_overrides(monkeypatch):
    monkeypatch.setenv("TRADING_BOT_GIT_SHA", "abc123")
    monkeypatch.setattr(release_metadata_module, "_DEPLOYMENT_ID", "deployment-1")
    release_metadata_module.release_metadata.cache_clear()

    metadata = release_metadata_module.release_metadata()

    assert metadata["git_commit"] == "abc123"
    assert metadata["deployment_id"] == "deployment-1"
    assert metadata["code_version"]
    release_metadata_module.release_metadata.cache_clear()


def test_direct_metrics_payload_includes_release_identity():
    release = {
        "code_version": "1.4.11",
        "git_commit": "abc123",
        "deployment_id": "deployment-1",
    }
    sink = SupabaseRestMetricsSink(
        "https://example.supabase.co",
        "anon-key",
        release=release,
    )
    metric = OperationalMetric(
        name="heartbeat",
        value=1.0,
        timestamp=datetime(2026, 9, 23, 21, 0),
        idempotency_key="heartbeat-1",
    )

    payload = sink._payload_for_metric(metric)

    assert {key: payload[key] for key in release} == release
