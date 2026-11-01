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
    SegmentRole,
    ValidationCategory,
)
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.methodology import (
    FinalHoldoutAuthorization,
    FrozenStudyDefinition,
)
from vibe.research_pipeline.publishing import (
    BackoffPolicy,
    PublicationKind,
    ReconciliationStatus,
    RemoteAcknowledgement,
    RemoteRecordState,
    ResearchHttpResponse,
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
from vibe.research_pipeline.reliability import (
    ReliabilityPolicy,
    ReliabilityVerdict,
    StudyReliabilityReport,
)
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
        existing = self.rows.get(publication.idempotency_key)
        if existing and existing.payload_sha256 != publication.payload_sha256:
            raise RuntimeError("remote immutable payload divergence")
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
        {"identity": {"trade_data": []}},
        {"identity": {"fill_records": []}},
        {"identity": {"equity_history": []}},
        {"identity": {"bar_series": []}},
    ],
)
def test_forbidden_nested_aliases_are_rejected(payload: dict[str, Any]):
    with pytest.raises(StoreError, match="event-level data"):
        validate_publication_payload(PublicationKind.RUN_SUMMARY, payload)


def test_experiment_decision_events_are_separate_safe_publications(tmp_path: Path):
    store = SqliteResearchStore(
        tmp_path / "research.sqlite3", origin_device_id="phase-1-device"
    )
    policy = ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "ORB duration stability",
        {"axis": "opening_range_minutes", "values": [5, 15, 30]},
        reliability_policy=policy,
    )
    run_id = store.register_run(
        fingerprint=_fingerprint("orb"),
        methodology_version="nested-walk-forward-v1",
        segment_role=SegmentRole.TEST,
    )
    store.link_experiment_run(
        experiment_id, 0, "orb-15", SegmentRole.TEST, run_id
    )
    store.finish_experiment(experiment_id, "completed", {"selected": "orb-15"})
    report = StudyReliabilityReport(
        verdict=ReliabilityVerdict.RELIABLE,
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        engine_integrity_complete=True,
        procedure_state="completed",
        stitched_oos_summary={"expectancy_r": 0.12},
        fold_stability_summary={"positive_fold_fraction": 0.8},
        robustness_summary={},
        holdout_summary={"status": "locked"},
        statistical_confidence={"interval_crosses_zero": False},
        verdict_reason="All registered criteria passed.",
        criteria=(),
    )
    store.record_study_reliability(experiment_id, policy, report)
    definition = FrozenStudyDefinition.freeze(
        strategy_id="orb",
        ruleset_identity={"sha256": "rules"},
        universe_identity={"symbols": ["QQQ"]},
        dataset_ids=("dataset-1",),
        parameter_axes=(
            {"name": "opening_range_minutes", "values": [5, 15, 30]},
        ),
        split_policy={"kind": "nested"},
        warmup_policy={"sessions": 50},
        execution_model_version=6,
        metric_calculation_version=2,
        leakage_requirements=("purged",),
        reliability_policy=policy,
        final_holdout_lock_hash="sha256:locked",
        frozen_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
    )
    store.freeze_study(experiment_id, definition)
    authorization = FinalHoldoutAuthorization(
        token="0123456789abcdef",
        reason="Run the locked final holdout",
    )
    store.authorize_final_holdout(experiment_id, authorization)
    claim = store.claim_final_holdout(experiment_id, authorization.token)
    store.interrupt_final_holdout(claim, "Simulated runner crash")

    rows = store._conn.execute(
        """SELECT idempotency_key, aggregate_type, parent_idempotency_key,
                  payload_json
           FROM publication_outbox
           WHERE aggregate_id = ?
           ORDER BY sequence""",
        (experiment_id,),
    ).fetchall()
    assert [row["aggregate_type"] for row in rows] == [
        "experiment_definition",
        "experiment_membership",
        "experiment_outcome",
        "study_reliability",
        "methodology_freeze",
        "holdout_authorization",
        "holdout_claim",
        "holdout_terminal",
    ]
    assert rows[1]["parent_idempotency_key"] == (
        f"experiment:{experiment_id}:definition"
    )
    for row in rows:
        payload = json.loads(row["payload_json"])
        validate_publication_payload(
            PublicationKind(row["aggregate_type"]), payload
        )
        assert row["idempotency_key"].startswith(f"experiment:{experiment_id}:")
        assert "token" not in row["payload_json"].lower()
        assert "authorization_sha256" not in row["payload_json"]


def test_experiment_outcome_and_publication_enqueue_are_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = store.start_experiment("Atomic experiment", {})

    def fail_enqueue(**_: Any) -> None:
        raise RuntimeError("simulated experiment publication failure")

    monkeypatch.setattr(store, "_insert_publication_tx", fail_enqueue)
    with pytest.raises(RuntimeError, match="publication failure"):
        store.finish_experiment(experiment_id, "completed", {"ok": True})

    row = store._conn.execute(
        """SELECT state, result_json FROM research_experiments
           WHERE experiment_id = ?""",
        (experiment_id,),
    ).fetchone()
    assert row["state"] == "running"
    assert row["result_json"] is None


def test_experiment_publication_rejects_secret_fields():
    with pytest.raises(StoreError, match="event-level data"):
        validate_publication_payload(
            PublicationKind.HOLDOUT_AUTHORIZATION,
            {
                "identity": {"experiment_id": "exp"},
                "authorization": {"authorization_token": "secret"},
                "eligibility": {},
                "timestamps": {},
            },
        )
    with pytest.raises(StoreError, match="possible secret"):
        validate_publication_payload(
            PublicationKind.HOLDOUT_AUTHORIZATION,
            {
                "identity": {"experiment_id": "exp"},
                "authorization": {
                    "reason": "Authorization: Bearer super-secret-value"
                },
                "eligibility": {},
                "timestamps": {},
            },
        )


def test_experiment_publication_backfill_is_explicit_and_idempotent(
    tmp_path: Path,
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = store.start_experiment("Legacy experiment", {})
    store.finish_experiment(experiment_id, "inconclusive", {"reason": "legacy"})
    store._conn.execute(
        """DELETE FROM publication_outbox
           WHERE idempotency_key = ?""",
        (f"experiment:{experiment_id}:outcome",),
    )
    store._conn.execute(
        """DELETE FROM publication_outbox
           WHERE idempotency_key = ?""",
        (f"experiment:{experiment_id}:definition",),
    )
    store._conn.commit()

    assert store.backfill_experiment_publications(experiment_id) == 2
    assert store.backfill_experiment_publications(experiment_id) == 0
    rows = store._conn.execute(
        """SELECT aggregate_type FROM publication_outbox
           WHERE aggregate_id = ? ORDER BY sequence""",
        (experiment_id,),
    ).fetchall()
    assert [row["aggregate_type"] for row in rows] == [
        "experiment_definition",
        "experiment_outcome",
    ]


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
    with store._conn:
        store._conn.execute(
            """
            UPDATE publication_outbox
            SET status = 'published', published_at = ?
            WHERE idempotency_key = ?
            """,
            (datetime.now(timezone.utc).isoformat(), f"run:{first}"),
        )
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

    assert reconciler.repair(report, reason="repair missing remote row") == 1
    first_row = store._conn.execute(
        """
        SELECT status, publication_version FROM publication_outbox
        WHERE idempotency_key = ?
        """,
        (f"run:{first}",),
    ).fetchone()
    second_row = store._conn.execute(
        """
        SELECT status, publication_version FROM publication_outbox
        WHERE idempotency_key = ?
        """,
        (f"run:{second}",),
    ).fetchone()
    assert first_row["status"] == "published"
    assert first_row["publication_version"] == 1
    assert second_row["publication_version"] == 2

    publisher = ResearchPublisher(store, destination)
    assert (await publisher.flush_once()).claimed == 1
    repaired_report = await reconciler.compare()
    repaired_statuses = {
        item.idempotency_key: item.status for item in repaired_report.items
    }
    assert repaired_statuses[f"run:{first}"] is ReconciliationStatus.DIVERGENT
    assert repaired_statuses[f"run:{second}"] is ReconciliationStatus.MATCHED
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


def test_expired_claim_cannot_complete_or_fail_reclaimed_lease(tmp_path: Path):
    path = tmp_path / "research.sqlite3"
    worker_a = SqliteResearchStore(path)
    _terminal_run(worker_a, "lease-race")
    worker_b = SqliteResearchStore(path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    publication_a = worker_a.claim_publications(
        limit=1, claimed_by="worker-a", lease_seconds=30, now=start
    )[0]
    assert worker_b.reset_stale_publication_claims(
        now=start + timedelta(seconds=31)
    ) == 1
    publication_b = worker_b.claim_publications(
        limit=1,
        claimed_by="worker-b",
        lease_seconds=30,
        now=start + timedelta(seconds=31),
    )[0]
    assert publication_a.claim_token != publication_b.claim_token
    acknowledgement = RemoteAcknowledgement(
        publication_b.idempotency_key,
        publication_b.payload_sha256,
        publication_b.publication_version,
        start.isoformat(),
    )

    assert not worker_a.mark_publication_succeeded(
        publication_a, acknowledgement, now=start + timedelta(seconds=32)
    )
    assert not worker_a.mark_publication_failed(
        publication_a,
        error="stale worker failure",
        next_attempt_at=start + timedelta(seconds=40),
        dead_letter=False,
        now=start + timedelta(seconds=32),
    )
    assert worker_b.mark_publication_succeeded(
        publication_b, acknowledgement, now=start + timedelta(seconds=32)
    )
    worker_a.close()
    worker_b.close()


def _registry_payload(execution_identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "record_type": "experiment",
            "record_id": "EXP-999",
            "methodology_version": "research-store-v1",
            "execution_model_version": 6,
            "execution_identity": dict(execution_identity),
            "record_origin": "research_store",
            "origin_device_id": "pytest",
        },
        "status": "completed",
        "checksum": {"canonical_sha256": "a" * 64},
        "lineage": [],
        "badges": ["research-store-v1", "execution-model-v6"],
        "timestamps": {"recorded_at": datetime.now(timezone.utc).isoformat()},
    }


@pytest.mark.parametrize(
    "ticks",
    [
        {"STOP": {"nested": 2}},
        {"STOP": [2]},
        {"STOP": float("nan")},
        {"STOP": float("inf")},
        {"STOP": -1},
        {"STOP": 1_000_001},
        {"TRADE": 2},
        {"stop": 2},
    ],
)
def test_exit_slippage_ticks_require_exact_bounded_numeric_mapping(ticks: Any):
    with pytest.raises(StoreError, match="exit_slippage_ticks"):
        validate_publication_payload(
            PublicationKind.REGISTRY_RECORD,
            _registry_payload({"exit_slippage_ticks": ticks}),
        )


def test_public_store_api_rolls_back_invalid_execution_identity(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    with pytest.raises(StoreError, match="exit_slippage_ticks"):
        store.create_registry_record(
            record_type="experiment",
            payload={
                "id": "EXP-001",
                "status": "completed",
                "execution_metadata": {"git_dirty": False},
            },
            methodology_version="research-store-v1",
            execution_identity={
                "execution_model_version": 6,
                "exit_slippage_ticks": {"STOP": {"trade_data": [1]}},
            },
        )
    assert store.get_registry_record(
        record_type="experiment", legacy_id="EXP-001"
    ) is None
    store.close()


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


@pytest.mark.parametrize("starting_version", [1, 2, 3, 4, 5, 6])
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
    def __init__(
        self,
        response_status: int = 200,
        response_body: Any = None,
        *,
        responses: Optional[list[ResearchHttpResponse]] = None,
    ) -> None:
        self.response_status = response_status
        self.response_body = response_body
        self.responses = list(responses or [])
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
    ) -> ResearchHttpResponse:
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
        if self.responses:
            return self.responses.pop(0)
        return ResearchHttpResponse(
            self.response_status, self.response_body, {}
        )


class RaisingHttpTransport:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def request(self, *_: Any, **__: Any) -> ResearchHttpResponse:
        raise self.error


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


@pytest.mark.asyncio
async def test_supabase_adapter_sends_auth_and_redacts_boundaries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    _terminal_run(store, "adapter-auth")
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
    settings = ResearchSupabaseSettings(
        url="https://example.supabase.co", service_role_key=secret
    )
    destination = SupabaseResearchDestination(settings, transport=transport)
    result = await destination.publish(publication)

    assert result.idempotency_key == publication.idempotency_key
    assert transport.requests[0]["headers"] == {
        "apikey": secret,
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    assert secret not in repr(settings)
    assert secret not in repr(destination.settings)

    failing = SupabaseResearchDestination(
        settings,
        transport=FakeHttpTransport(
            401,
            {
                "message": (
                    f"apikey={secret} Authorization: Bearer {secret} "
                    f"service_role_key={secret}"
                )
            },
        ),
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError) as error:
            await failing.publish(publication)
    assert secret not in str(error.value)
    assert secret not in caplog.text

    transport_failure = SupabaseResearchDestination(
        settings,
        transport=RaisingHttpTransport(
            RuntimeError(f"Authorization: Bearer {secret}")
        ),
    )
    with pytest.raises(RuntimeError) as transport_error:
        await transport_failure.publish(publication)
    assert secret not in str(transport_error.value)

    publisher_clock = datetime.now(timezone.utc) + timedelta(seconds=61)
    publisher = ResearchPublisher(
        store,
        transport_failure,
        clock=lambda: publisher_clock,
    )
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert (await publisher.flush_once()).failed == 1
    assert secret not in caplog.text
    store.close()


@pytest.mark.asyncio
async def test_remote_state_paginates_past_1000_rows_before_reconciliation(
    tmp_path: Path,
):
    secret = "pagination-service-role-secret"
    rows = [
        {
            "idempotency_key": f"remote:{index:04d}",
            "payload_sha256": f"{index:064x}",
            "publication_version": 1,
        }
        for index in range(1001)
    ]
    transport = FakeHttpTransport(
        responses=[
            ResearchHttpResponse(
                206, rows[:1000], {"Content-Range": "0-999/1001"}
            ),
            ResearchHttpResponse(
                206, rows[1000:], {"Content-Range": "1000-1000/1001"}
            ),
        ]
    )
    destination = SupabaseResearchDestination(
        ResearchSupabaseSettings(
            url="https://example.supabase.co", service_role_key=secret
        ),
        transport=transport,
    )
    state = await destination.list_state()
    assert len(state) == 1001
    assert [request["headers"]["Range"] for request in transport.requests] == [
        "0-999",
        "1000-1999",
    ]

    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _terminal_run(store, "later-page")
    local = store.local_publication_state()[0]
    paged_destination = SupabaseResearchDestination(
        ResearchSupabaseSettings(
            url="https://example.supabase.co", service_role_key=secret
        ),
        transport=FakeHttpTransport(
            responses=[
                ResearchHttpResponse(
                    206, rows[:1000], {"Content-Range": "0-999/1001"}
                ),
                ResearchHttpResponse(
                    206,
                    [
                        {
                            "idempotency_key": local.idempotency_key,
                            "payload_sha256": local.payload_sha256,
                            "publication_version": local.publication_version,
                        }
                    ],
                    {"Content-Range": "1000-1000/1001"},
                ),
            ]
        ),
    )
    reconciler = ResearchReconciler(store, paged_destination)
    report = await reconciler.compare()
    item = next(
        item for item in report.items if item.idempotency_key == f"run:{run_id}"
    )
    assert item.status is ReconciliationStatus.MATCHED
    assert reconciler.repair(report, reason="repair missing only") == 0
    store.close()
