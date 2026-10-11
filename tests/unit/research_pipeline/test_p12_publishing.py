"""P12/F12 tests for durable summary-only research publication."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pytest

from vibe.research_pipeline.contracts import (
    RetentionProfile,
    RunEvidence,
    ValidationCategory,
)
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.publishing import (
    BackoffPolicy,
    PublicationKind,
    ReconciliationStatus,
    RemoteAcknowledgement,
    RemoteRecordState,
    ResearchPublication,
    ResearchPublisher,
    ResearchReconciler,
    ResearchSupabaseSettings,
    SupabaseResearchDestination,
    redact_secrets,
    validate_publication_payload,
)
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.store import StoreError
from vibe.research_pipeline.validation import ValidationReport, ValidationScope


def _fingerprint(strategy_id: str) -> RunFingerprint:
    return RunFingerprint(
        strategy_id=strategy_id,
        ruleset_content_sha256="a" * 64,
        parameters={"opening_range_minutes": 15},
        universe_hash="b" * 64,
        split_manifest_hash="c" * 64,
        data_snapshot_id="fixture-2026-09",
        feature_set_version=3,
        execution_model_version=6,
        metric_calculation_version=2,
        code_commit="1234567abcdef",
        code_dirty=False,
    )


def _report(target: RunState = RunState.COMPLETED) -> ValidationReport:
    return ValidationReport(
        profile_name="p12",
        scope=ValidationScope.RUN,
        target_state=target,
        findings=(),
        checked_categories=tuple(ValidationCategory),
        registry_hash_at_execution="d" * 64,
        current_registry_hash="d" * 64,
        leakage_passed=True,
    )


def _evidence() -> RunEvidence:
    return RunEvidence(
        trade_count=2,
        trade_ledger_sha256="1" * 64,
        equity_curve_sha256="2" * 64,
        equity_points=25,
        first_session=date(2026, 1, 2),
        last_session=date(2026, 1, 5),
        starting_equity=100_000.0,
        ending_equity=100_250.0,
        gross_pnl=300.0,
        total_costs=50.0,
        net_pnl=250.0,
        max_observed_leverage=1.0,
        min_cash=50_000.0,
        retention_profile=RetentionProfile.SUMMARY,
    )


def _terminal_run(store: SqliteResearchStore, strategy_id: str) -> str:
    run_id = store.register_run(
        fingerprint=_fingerprint(strategy_id),
        methodology_version="research-store-v1",
        lineage_id=f"lineage-{strategy_id}",
    )
    token = store.start_run(run_id, owner="pytest", lease_seconds=60)
    store.record_metrics(
        run_id, {"expectancy_r": 0.25, "net_pnl": 250.0}, calculation_version=2
    )
    store.record_evidence(run_id, _evidence())
    store.transition(
        run_id, target=RunState.VALIDATING, lease_token=token
    )
    store.finalize_validation(run_id, _report())
    return run_id


@dataclass
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class MemoryDestination:
    def __init__(
        self, *, fail_before: int = 0, fail_after_apply: int = 0
    ) -> None:
        self.fail_before = fail_before
        self.fail_after_apply = fail_after_apply
        self.calls: list[str] = []
        self.rows: dict[str, RemoteRecordState] = {}

    async def publish(
        self, publication: ResearchPublication
    ) -> RemoteAcknowledgement:
        self.calls.append(publication.idempotency_key)
        if self.fail_before:
            self.fail_before -= 1
            raise ConnectionError("offline")
        state = RemoteRecordState(
            publication.idempotency_key,
            publication.payload_sha256,
            publication.publication_version,
        )
        self.rows[publication.idempotency_key] = state
        if self.fail_after_apply:
            self.fail_after_apply -= 1
            raise ConnectionError("acknowledgement interrupted")
        return RemoteAcknowledgement(
            publication.idempotency_key,
            publication.payload_sha256,
            publication.publication_version,
            datetime.now(timezone.utc).isoformat(),
        )

    async def list_state(self) -> Sequence[RemoteRecordState]:
        return tuple(self.rows.values())


def test_finalization_and_outbox_enqueue_are_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = store.register_run(
        fingerprint=_fingerprint("atomic"), methodology_version="v1"
    )
    token = store.start_run(run_id, owner="pytest", lease_seconds=60)
    store.transition(run_id, target=RunState.VALIDATING, lease_token=token)

    def fail_enqueue(**_: Any) -> None:
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(store, "_insert_publication_tx", fail_enqueue)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        store.finalize_validation(run_id, _report())

    assert store.get_run(run_id).state is RunState.VALIDATING
    assert store.get_validation_report(run_id) is None
    assert store.get_outbox(run_id) is None
    store.close()


def test_terminal_summary_contains_only_approved_aggregates_and_identities(
    tmp_path: Path,
):
    store = SqliteResearchStore(
        tmp_path / "research.sqlite3", origin_device_id="pytest-device"
    )
    run_id = _terminal_run(store, "approved")
    row = store.get_outbox(run_id)
    payload = json.loads(row["payload_json"])

    assert payload["identity"]["origin_device_id"] == "pytest-device"
    assert payload["identity"]["execution_model_version"] == 6
    assert payload["identity"]["badges"] == [
        "research-store-v1",
        "execution-model-v6",
    ]
    assert payload["evidence"]["aggregate_trade_count"] == 2
    assert payload["evidence"]["trade_ledger_sha256"] == "1" * 64
    assert payload["evidence"]["equity_curve_sha256"] == "2" * 64
    assert "equity_curve" not in payload["evidence"]
    assert "trades" not in row["payload_json"]
    store.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"identity": {"tradeLedger": []}},
        {"identity": {"nested": {"individual_fills": []}}},
        {"identity": {"nested": {"raw-bars": []}}},
        {"identity": {"nested": {"EquityPoints": []}}},
    ],
)
def test_forbidden_nested_aliases_are_rejected(payload: dict[str, Any]):
    with pytest.raises(StoreError, match="event-level data"):
        validate_publication_payload(PublicationKind.RUN_SUMMARY, payload)


@pytest.mark.asyncio
async def test_f12_restart_recovers_in_order_without_duplicates(tmp_path: Path):
    path = tmp_path / "research.sqlite3"
    writer = SqliteResearchStore(path)
    first = _terminal_run(writer, "first")
    second = _terminal_run(writer, "second")
    writer.close()

    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    destination = MemoryDestination(fail_before=1)
    restarted = SqliteResearchStore(path)
    publisher = ResearchPublisher(
        restarted,
        destination,
        max_attempts=3,
        backoff=BackoffPolicy(
            base_seconds=10, maximum_seconds=10, jitter_fraction=0
        ),
        clock=clock,
    )

    failed = await publisher.flush_once()
    assert failed.failed == 1
    assert destination.calls == [f"run:{first}"]
    assert (await publisher.flush_once()).claimed == 0

    clock.advance(10)
    assert (await publisher.flush_once()).published == 1
    assert (await publisher.flush_once()).published == 1
    assert destination.calls == [f"run:{first}", f"run:{first}", f"run:{second}"]
    assert set(destination.rows) == {f"run:{first}", f"run:{second}"}
    restarted.close()


@pytest.mark.asyncio
async def test_duplicate_delivery_after_lost_ack_upserts_exactly_once(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _terminal_run(store, "lost-ack")
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    destination = MemoryDestination(fail_after_apply=1)
    publisher = ResearchPublisher(
        store,
        destination,
        max_attempts=3,
        backoff=BackoffPolicy(
            base_seconds=1, maximum_seconds=1, jitter_fraction=0
        ),
        clock=clock,
    )

    assert (await publisher.flush_once()).failed == 1
    assert len(destination.rows) == 1
    clock.advance(1)
    assert (await publisher.flush_once()).published == 1
    assert destination.calls == [f"run:{run_id}", f"run:{run_id}"]
    assert len(destination.rows) == 1
    store.close()


@pytest.mark.asyncio
async def test_backoff_dead_letter_and_explicit_requeue(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _terminal_run(store, "dead-letter")
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    destination = MemoryDestination(fail_before=2)
    publisher = ResearchPublisher(
        store,
        destination,
        max_attempts=2,
        backoff=BackoffPolicy(
            base_seconds=4, maximum_seconds=4, jitter_fraction=0
        ),
        clock=clock,
    )

    assert (await publisher.flush_once()).failed == 1
    row = store.get_outbox(run_id)
    assert row["attempts"] == 1
    assert datetime.fromisoformat(row["next_attempt_at"]) == clock.now + timedelta(
        seconds=4
    )
    clock.advance(4)
    assert (await publisher.flush_once()).dead_lettered == 1
    row = store.get_outbox(run_id)
    assert row["status"] == "dead_letter"
    assert row["last_error"] == "offline"

    event_id = store._conn.execute(
        "SELECT event_id FROM publication_outbox WHERE idempotency_key = ?",
        (f"run:{run_id}",),
    ).fetchone()["event_id"]
    assert store.requeue_dead_letter(
        event_id, reason="network restored", now=clock.now
    )
    assert (await publisher.flush_once()).published == 1
    store.close()


@pytest.mark.asyncio
async def test_reconciliation_identifies_and_repairs_only_safe_local_records(
    tmp_path: Path,
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    first = _terminal_run(store, "reconcile-a")
    second = _terminal_run(store, "reconcile-b")
    destination = MemoryDestination()
    local_states = {
        item.idempotency_key: item for item in store.local_publication_state()
    }
    first_state = local_states[f"run:{first}"]
    destination.rows = {
        first_state.idempotency_key: RemoteRecordState(
            first_state.idempotency_key, "0" * 64, 1
        ),
        "remote:orphan": RemoteRecordState("remote:orphan", "f" * 64, 1),
    }
    reconciler = ResearchReconciler(store, destination)

    report = await reconciler.compare()
    statuses = {item.idempotency_key: item.status for item in report.items}
    assert statuses[f"run:{first}"] is ReconciliationStatus.DIVERGENT
    assert statuses[f"run:{second}"] is ReconciliationStatus.MISSING_REMOTE
    assert statuses["remote:orphan"] is ReconciliationStatus.ORPHANED_REMOTE

    assert reconciler.repair(report, reason="operator-approved checksum repair") == 2
    assert "remote:orphan" not in {
        row["idempotency_key"]
        for row in store._conn.execute(
            "SELECT idempotency_key FROM publication_outbox"
        ).fetchall()
    }
    store.close()


def test_registry_publications_preserve_parent_order_and_identity_badges(
    tmp_path: Path,
):
    store = SqliteResearchStore(
        tmp_path / "research.sqlite3", origin_device_id="legacy-importer"
    )
    store.create_registry_record(
        record_type="hypothesis",
        payload={
            "id": "HYP-001",
            "status": "validated",
            "title": "Parent",
        },
        methodology_version="legacy-uncontrolled",
    )
    store.create_registry_record(
        record_type="experiment",
        payload={
            "id": "EXP-001",
            "status": "completed",
            "hypothesis_id": "HYP-001",
            "execution_metadata": {"git_dirty": False},
        },
        methodology_version="legacy-uncontrolled",
    )
    rows = store._conn.execute(
        """
        SELECT sequence, aggregate_id, parent_idempotency_key, payload_json
        FROM publication_outbox ORDER BY sequence
        """
    ).fetchall()
    assert rows[0]["aggregate_id"] == "hypothesis:HYP-001"
    assert rows[1]["aggregate_id"] == "experiment:EXP-001"
    assert rows[1]["parent_idempotency_key"]
    payload = json.loads(rows[1]["payload_json"])
    assert payload["badges"] == ["legacy-uncontrolled", "unknown-pre-p2b"]
    store.close()


def test_concurrent_claims_use_a_single_lease(tmp_path: Path):
    path = tmp_path / "research.sqlite3"
    first = SqliteResearchStore(path)
    _terminal_run(first, "lease")
    second = SqliteResearchStore(path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    claimed_first = first.claim_publications(
        limit=10, claimed_by="worker-a", lease_seconds=60, now=now
    )
    claimed_second = second.claim_publications(
        limit=10, claimed_by="worker-b", lease_seconds=60, now=now
    )
    assert len(claimed_first) == 1
    assert claimed_second == []
    first.close()
    second.close()


@pytest.mark.asyncio
async def test_published_payload_is_immutable_in_database(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _terminal_run(store, "immutable-publication")
    publisher = ResearchPublisher(store, MemoryDestination())
    assert (await publisher.flush_once()).published == 1

    with pytest.raises(sqlite3.IntegrityError, match="payload is immutable"):
        with store._conn:
            store._conn.execute(
                """
                UPDATE publication_outbox SET payload_json = '{}'
                WHERE idempotency_key = ?
                """,
                (f"run:{run_id}",),
            )
    store.close()


@pytest.mark.parametrize("starting_version", [1, 2, 3, 4, 5])
def test_migration_from_every_prior_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, starting_version: int
):
    path = tmp_path / f"v{starting_version}.sqlite3"
    conn = sqlite3.connect(path)
    schema.configure_connection(conn)
    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(
            schema, "_MIGRATIONS", schema._MIGRATIONS[:starting_version]
        )
        migration_patch.setattr(schema, "SCHEMA_VERSION", starting_version)
        assert schema.migrate(conn) == starting_version
    conn.close()

    upgraded = SqliteResearchStore(path)
    versions = [
        row[0]
        for row in upgraded._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]
    assert versions == list(range(1, schema.SCHEMA_VERSION + 1))
    upgraded.close()


def test_failure_visibility_reports_status_and_age(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    _terminal_run(store, "visibility")
    counts = store.publication_status_counts()
    assert counts["pending"] == 1
    assert set(counts) == {
        "pending",
        "publishing",
        "failed",
        "published",
        "dead_letter",
    }
    assert store.pending_publications_older_than(
        timedelta(0), now=datetime.now(timezone.utc) + timedelta(seconds=1)
    ) == 1
    store.close()


def test_research_credentials_are_separate_and_redacted():
    secret = "research-service-role-secret"
    settings = ResearchSupabaseSettings.from_env(
        {
            "RESEARCH_SUPABASE_URL": "https://example.supabase.co",
            "RESEARCH_SUPABASE_SERVICE_ROLE_KEY": secret,
        }
    )
    assert secret not in repr(settings)
    assert secret not in redact_secrets(
        f"Authorization: Bearer {secret} service_role_key={secret}", (secret,)
    )
    with pytest.raises(ValueError, match="separate"):
        ResearchSupabaseSettings.from_env(
            {
                "RESEARCH_SUPABASE_URL": "https://example.supabase.co",
                "RESEARCH_SUPABASE_SERVICE_ROLE_KEY": secret,
                "TRADING_SUPABASE_SERVICE_ROLE_KEY": secret,
            }
        )
    with pytest.raises(ValueError, match="NEXT_PUBLIC"):
        ResearchSupabaseSettings.from_env(
            {
                "RESEARCH_SUPABASE_URL": "https://example.supabase.co",
                "RESEARCH_SUPABASE_SERVICE_ROLE_KEY": secret,
                "NEXT_PUBLIC_RESEARCH_SUPABASE_SERVICE_ROLE_KEY": secret,
            }
        )


class FakeHttpTransport:
    def __init__(self, response_status: int, response_body: Any) -> None:
        self.response_status = response_status
        self.response_body = response_body
        self.requests: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        timeout_seconds: float,
    ) -> tuple[int, Any]:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "params": dict(params or {}),
                "json": json_body,
                "timeout": timeout_seconds,
            }
        )
        return self.response_status, self.response_body


@pytest.mark.asyncio
async def test_supabase_adapter_uses_injected_transport_and_redacts_errors(
    tmp_path: Path,
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    _terminal_run(store, "adapter")
    publication = store.claim_publications(
        limit=1,
        claimed_by="adapter-test",
        lease_seconds=60,
        now=datetime.now(timezone.utc),
    )[0]
    secret = "service-role-secret-value"
    acknowledgement = {
        "idempotency_key": publication.idempotency_key,
        "payload_sha256": publication.payload_sha256,
        "publication_version": publication.publication_version,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }
    transport = FakeHttpTransport(201, [acknowledgement])
    destination = SupabaseResearchDestination(
        ResearchSupabaseSettings(
            url="https://example.supabase.co", service_role_key=secret
        ),
        transport=transport,
    )
    result = await destination.publish(publication)
    assert result.idempotency_key == publication.idempotency_key
    assert transport.requests[0]["headers"]["Authorization"] == f"Bearer {secret}"
    assert secret not in repr(destination.settings)

    failing = SupabaseResearchDestination(
        ResearchSupabaseSettings(
            url="https://example.supabase.co", service_role_key=secret
        ),
        transport=FakeHttpTransport(
            401, {"message": f"apikey={secret} authorization={secret}"}
        ),
    )
    with pytest.raises(RuntimeError) as error:
        await failing.publish(publication)
    assert secret not in str(error.value)
    store.close()
