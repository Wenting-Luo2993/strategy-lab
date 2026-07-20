"""Tests for remote dashboard outbox publishing."""

from datetime import datetime, timedelta

import pytest

from vibe.trading_bot.publishing.remote_data_publisher import RemoteDataPublisher
from vibe.trading_bot.storage.dashboard_store import PublishOutboxEvent, PublishOutboxStore


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
    outbox.enqueue_event(_event("trade:pending", old_time))
    publisher = RemoteDataPublisher(outbox, FakeDestination(), batch_size=1)

    await publisher.flush_pending(timeout_seconds=5, max_batches=1)

    deleted = publisher.prune_published_before(old_time + timedelta(days=1))

    assert deleted == 1
    assert outbox.get_event("trade:published") is None
    assert outbox.get_event("trade:pending") is not None
    outbox.close()