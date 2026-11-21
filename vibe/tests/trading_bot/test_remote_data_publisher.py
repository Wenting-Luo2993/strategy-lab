"""Tests for remote dashboard outbox publishing."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from vibe.trading_bot.publishing.remote_data_publisher import (
    PublishOutcome,
    RemoteDataPublisher,
    SupabaseRestDestination,
)
from vibe.trading_bot.storage.dashboard_store import PublishOutboxEvent, PublishOutboxStore
from vibe.trading_bot.publishing.recover_metrics import apply_recovery, main as recovery_main, recovery_plan


class FakeDestination:
    def __init__(self, failures_before_success: int = 0):
        self.failures_before_success = failures_before_success
        self.published = []

    async def publish(self, event):
        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            raise RuntimeError("remote unavailable")
        self.published.append(event)


def _event(event_id: str = "trade:1", next_retry_at: datetime | None = None) -> PublishOutboxEvent:
    event_time = datetime(2026, 7, 20, 13, 30)
    return PublishOutboxEvent(
        event_id=event_id,
        event_type="upsert",
        aggregate_type="trade",
        aggregate_id="1",
        destination="supabase",
        payload={"trade_id": "1", "entry_time": event_time.isoformat()},
        original_event_timestamp=event_time,
        next_retry_at=next_retry_at or event_time,
    )


def test_supabase_destination_filters_non_schema_metadata_for_snapshots():
    release = {
        "code_version": "1.4.11",
        "git_commit": "abc123",
        "deployment_id": "deployment-1",
    }
    destination = SupabaseRestDestination(
        "https://example.supabase.co",
        "service-key",
        release=release,
    )

    equity_payload = destination._payload_for_aggregate(
        "equity_snapshot",
        {
            "snapshot_id": "snap-1",
            "account_id": "acct",
            "timestamp": "2026-07-27T17:03:38Z",
            "net_liquidation": 10000,
            "source": "bot",
            "reason": "poll",
        },
    )
    position_payload = destination._payload_for_aggregate(
        "position",
        {
            "position_id": "acct:QQQ",
            "account_id": "acct",
            "symbol": "QQQ",
            "quantity": -1,
            "side": "short",
            "updated_at": "2026-07-27T17:03:38Z",
            "reason": "poll",
        },
    )

    assert "reason" not in equity_payload
    assert "reason" not in position_payload
    assert equity_payload["snapshot_id"] == "snap-1"
    assert position_payload["position_id"] == "acct:QQQ"
    assert {key: equity_payload[key] for key in release} == release
    assert {key: position_payload[key] for key in release} == release
    metric_payload = destination._payload_for_aggregate(
        "metric",
        {
            "metric_id": "exec-1:slippage_bps",
            "metric_name": "slippage_bps",
            "metric_value": 1.2,
            "timestamp": "2026-07-27T17:03:38Z",
            "unexpected": "discard",
        },
    )
    assert destination.CONFLICT_BY_AGGREGATE["metric"] == "metric_id"
    assert metric_payload["metric_id"] == "exec-1:slippage_bps"
    assert {key: metric_payload[key] for key in release} == release
    assert "unexpected" not in metric_payload


@pytest.mark.parametrize("aggregate_type", SupabaseRestDestination.TABLE_BY_AGGREGATE)
def test_supabase_destination_adds_release_identity_to_every_aggregate(aggregate_type):
    release = {
        "code_version": "1.4.11",
        "git_commit": "abc123",
        "deployment_id": "deployment-1",
    }
    destination = SupabaseRestDestination(
        "https://example.supabase.co",
        "service-key",
        release=release,
    )

    payload = destination._payload_for_aggregate(aggregate_type, {})

    assert payload == release


def test_outbox_store_indexes_publication_ledger_identity(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))

    index = outbox._get_connection().execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'index'
          AND name = 'idx_publication_ledger_logical_event'
        """
    ).fetchone()

    assert index is not None


def test_supabase_destination_distinguishes_applied_from_stale_fenced_rows():
    assert SupabaseRestDestination._upsert_outcome(
        [{"publication_version": 7}],
        7,
    ) == PublishOutcome.APPLIED
    assert SupabaseRestDestination._upsert_outcome(
        [{"publication_version": 8}],
        7,
    ) == PublishOutcome.STALE_REJECTED
    assert SupabaseRestDestination._upsert_outcome(
        [],
        7,
    ) == PublishOutcome.STALE_REJECTED


@pytest.mark.asyncio
async def test_flush_pending_publishes_and_marks_rows_published(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    outbox.enqueue_event(_event())
    destination = FakeDestination()
    publisher = RemoteDataPublisher(outbox, destination, batch_size=10)

    result = await publisher.flush_pending(timeout_seconds=5, max_batches=1)
    row = outbox.get_event("trade:1")

    assert result.claimed == 1
    assert result.published == 1
    assert row["status"] == "published"
    assert row["original_event_timestamp"] == "2026-07-20T13:30:00"
    assert destination.published[0]["payload"]["trade_id"] == "1"
    outbox.close()


def _failed_metric(outbox, name="metric:AMZN", error=None):
    observed = datetime(2026, 11, 16, 21, 6)
    event = PublishOutboxEvent(
        event_id=name, event_type="upsert", aggregate_type="metric",
        aggregate_id=name, destination="supabase",
        payload={"metric_id": name, "metric_name": "parity_symbol_matched",
                 "timestamp": observed.isoformat()},
        original_event_timestamp=observed, next_retry_at=observed,
    )
    outbox.enqueue_event(event)
    claimed = outbox.claim_pending(1, "worker", now=observed)[0]
    error = error or 'Supabase publish failed: status=409 body=' + json.dumps({
        "code": "23505",
        "message": 'duplicate key value violates unique constraint "operational_metrics_pkey"',
        "details": 'Key (metric_name, "timestamp")=(parity_symbol_matched, 2026-11-16) already exists.',
    })
    outbox.record_failure(claimed, error)
    outbox.mark_dead_letter(claimed["event_id"], error,
                           expected_payload_version=claimed["payload_version"])
    return event


@pytest.mark.asyncio
async def test_metric_recovery_preserves_evidence_and_resolves_only_after_publication(tmp_path):
    path = tmp_path / "outbox.db"
    outbox = PublishOutboxStore(str(path))
    _failed_metric(outbox)
    original = outbox.get_event("metric:AMZN")
    failures = outbox.get_failures()
    before = path.read_bytes()
    summary, _ = recovery_plan(path)
    assert path.read_bytes() == before
    assert summary["eligible"] == 1
    assert apply_recovery(path, summary["plan_sha"]) == 1
    rows = outbox.get_event_successors("metric:AMZN")
    assert rows[0] == original
    assert rows[1]["payload"] == original["payload"]
    assert rows[1]["original_event_timestamp"] == original["original_event_timestamp"]
    assert rows[1]["publication_version"] > original["publication_version"]
    assert outbox.get_failures() == failures
    publisher = RemoteDataPublisher(outbox, FakeDestination())
    assert publisher.publish_cooldown_summary()["dead_letter"] == 1
    result = await publisher.flush_pending(timeout_seconds=5, max_batches=1)
    assert result.published == 1
    assert publisher.publish_cooldown_summary() == {
        "recovered_dead_letter": 1, "published": 1,
    }
    outbox.prune_published_before(datetime(2100, 1, 1))
    assert publisher.publish_cooldown_summary() == {"recovered_dead_letter": 1}
    assert recovery_plan(path)[0]["eligible"] == 0
    assert outbox.count_by_status("dead_letter") == 1
    outbox.close()


def test_metric_recovery_excludes_other_failures_and_rejects_stale_preview(tmp_path):
    path = tmp_path / "outbox.db"
    outbox = PublishOutboxStore(str(path))
    event = _failed_metric(outbox)
    _failed_metric(outbox, "metric:MSFT", error="network unavailable")
    plan, candidates = recovery_plan(path)
    assert [row["event_id"] for row in candidates] == ["metric:AMZN"]
    outbox.enqueue_event(event)
    with pytest.raises(ValueError, match="plan changed"):
        apply_recovery(path, plan["plan_sha"])
    with pytest.raises(ValueError, match="predecessor changed"):
        outbox.enqueue_event(event, expected_dead_letter_id="metric:AMZN")
    assert len(outbox.get_event_successors("metric:AMZN")) == 2
    assert recovery_plan(path)[0]["eligible"] == 0
    outbox.close()


def test_metric_recovery_apply_requires_schema_and_preview_confirmation(tmp_path):
    with pytest.raises(SystemExit) as exc:
        recovery_main(["--apply", "--outbox-db", str(tmp_path / "missing.db")])
    assert exc.value.code == 2
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.asyncio
async def test_concurrent_publishers_cannot_overtake_inflight_predecessor(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    outbox.enqueue_event(_event())
    started = asyncio.Event()
    release = asyncio.Event()
    remote_versions = []

    class DelayedDestination:
        async def publish(self, event):
            version = event["payload"].get("version", 1)
            if version == 1:
                started.set()
                await release.wait()
            remote_versions.append(version)

    first = RemoteDataPublisher(
        outbox,
        DelayedDestination(),
        claimed_by="first",
    )
    second = RemoteDataPublisher(
        outbox,
        DelayedDestination(),
        claimed_by="second",
    )
    first_flush = asyncio.create_task(
        first.flush_pending(timeout_seconds=5, max_batches=1)
    )
    await started.wait()
    outbox.enqueue_event(PublishOutboxEvent(
        event_id="trade:1",
        event_type="upsert",
        aggregate_type="trade",
        aggregate_id="1",
        destination="supabase",
        payload={
            "trade_id": "1",
            "entry_time": "2026-07-20T13:30:00",
            "version": 2,
        },
        original_event_timestamp=datetime(2026, 7, 20, 13, 31),
        next_retry_at=datetime(2026, 7, 20, 13, 31),
    ))

    blocked = await second.flush_pending(timeout_seconds=1, max_batches=1)
    assert blocked.claimed == 0
    release.set()
    await first_flush
    published = await second.flush_pending(timeout_seconds=5, max_batches=1)

    assert published.published == 1
    assert remote_versions == [1, 2]
    assert outbox.get_event("trade:1")["status"] == "published"
    outbox.close()


@pytest.mark.asyncio
async def test_flush_pending_records_failure_and_schedules_retry(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    outbox.enqueue_event(_event())
    destination = FakeDestination(failures_before_success=1)
    publisher = RemoteDataPublisher(outbox, destination, max_attempts=3, retry_base_seconds=1)

    result = await publisher.flush_pending(timeout_seconds=5, max_batches=1)
    row = outbox.get_event("trade:1")
    failures = outbox.get_failures("trade:1")

    assert result.failed == 1
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["last_error"] == "remote unavailable"
    assert failures[0]["error"] == "remote unavailable"
    assert row["original_event_timestamp"] == "2026-07-20T13:30:00"
    outbox.close()


@pytest.mark.asyncio
async def test_flush_pending_recovers_stale_publishing_claim(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    old = datetime(2020, 1, 1)
    outbox.enqueue_event(_event(next_retry_at=old))
    assert len(outbox.claim_pending(1, "crashed-worker", now=old)) == 1
    destination = FakeDestination()
    publisher = RemoteDataPublisher(
        outbox,
        destination,
        claim_timeout_seconds=1,
    )

    result = await publisher.flush_pending(timeout_seconds=5, max_batches=1)

    assert result.published == 1
    assert outbox.get_event("trade:1")["status"] == "published"


@pytest.mark.asyncio
async def test_flush_pending_dead_letters_after_max_attempts(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    outbox.enqueue_event(_event())
    destination = FakeDestination(failures_before_success=1)
    publisher = RemoteDataPublisher(outbox, destination, max_attempts=1)

    result = await publisher.flush_pending(timeout_seconds=5, max_batches=1)
    row = outbox.get_event("trade:1")

    assert result.dead_lettered == 1
    assert row["status"] == "dead_letter"
    assert row["last_error"] == "remote unavailable"
    outbox.close()


def test_reconcile_sources_enqueues_publish_events_and_wakes_publisher(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    publisher = RemoteDataPublisher(outbox, FakeDestination())

    class SourceStore:
        def iter_publish_events(self, trading_day):
            assert trading_day == "2026-07-20"
            return [_event("trade:reconciled")]

    enqueued = publisher.reconcile_sources([SourceStore()], "2026-07-20")

    assert enqueued == 1
    assert outbox.count_by_status("pending") == 1
    assert publisher.wake_event.is_set()
    outbox.close()


@pytest.mark.asyncio
async def test_prune_published_before_only_deletes_published_rows(tmp_path):
    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    old_time = datetime(2026, 7, 20, 13, 30)
    outbox.enqueue_event(_event("trade:published", old_time))
    outbox.enqueue_event(_event("trade:pending", datetime.utcnow() + timedelta(days=1)))
    publisher = RemoteDataPublisher(outbox, FakeDestination(), batch_size=1)

    await publisher.flush_pending(timeout_seconds=5, max_batches=1)
    published_row = outbox.get_event("trade:published")

    deleted = publisher.prune_published_before(datetime.fromisoformat(published_row["published_at"]) + timedelta(seconds=1))

    assert deleted == 1
    assert outbox.get_event("trade:published") is None
    assert outbox.get_event("trade:pending") is not None
    outbox.close()


@pytest.mark.asyncio
async def test_stale_remote_fence_is_never_marked_published(tmp_path):
    class StaleDestination:
        async def publish(self, event):
            return PublishOutcome.STALE_REJECTED

    outbox = PublishOutboxStore(str(tmp_path / "outbox.db"))
    outbox.enqueue_event(_event())
    result = await RemoteDataPublisher(outbox, StaleDestination()).flush_pending(
        timeout_seconds=5,
        max_batches=1,
    )

    row = outbox.get_event("trade:1")
    assert result.published == 0
    assert result.stale_rejected == 1
    assert row["status"] == "superseded"
    assert outbox.is_published("trade:1") is False


def test_publication_version_survives_publish_prune_and_restart(tmp_path):
    path = str(tmp_path / "outbox.db")
    published_at = datetime(2026, 7, 20, 13, 30)
    first = PublishOutboxStore(path)
    first.enqueue_event(_event())
    assert first.get_event("trade:1")["publication_version"] == 1
    first.mark_published("trade:1", published_at)
    assert first.prune_published_before(published_at + timedelta(seconds=1)) == 1
    first.close()

    restarted = PublishOutboxStore(path)
    corrected = PublishOutboxEvent(
        **{
            **_event().__dict__,
            "payload": {"trade_id": "1", "entry_time": "2026-07-20T13:30:00", "pnl": 2.0},
        }
    )
    assert restarted.enqueue_event(corrected) is True
    row = restarted.get_event("trade:1")
    assert row["event_id"] == "trade:1::successor::1"
    assert row["publication_version"] == 2


def test_publication_version_allocation_is_atomic_across_store_instances(tmp_path):
    path = str(tmp_path / "outbox.db")
    first = PublishOutboxStore(path)
    second = PublishOutboxStore(path)
    now = datetime(2026, 7, 20, 13, 30)

    def enqueue(store, event_id):
        return store.enqueue_event(PublishOutboxEvent(
            event_id=event_id,
            event_type="upsert",
            aggregate_type="account",
            aggregate_id="DU123",
            destination="supabase",
            payload={"account_id": "DU123", "display_name": event_id},
            original_event_timestamp=now,
        ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda args: enqueue(*args),
            ((first, "account:first"), (second, "account:second")),
        ))

    assert results == [True, True]
    versions = [
        row[0]
        for row in first._get_connection().execute(
            "SELECT publication_version FROM publish_outbox ORDER BY publication_version"
        ).fetchall()
    ]
    assert versions == [1, 2]