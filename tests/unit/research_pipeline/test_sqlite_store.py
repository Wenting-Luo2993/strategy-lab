"""Tests for the SQLite research store (P7).

Each test names the defect it prevents. The store's whole reason to exist is to
make impossible-in-the-old-world states structurally impossible here: skipped
validation, mutated terminal runs, and per-trade data escaping to the cloud.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timezone
from pathlib import Path

import pytest

from vibe.research_pipeline.contracts import (
    RetentionProfile,
    RunEvidence,
    Severity,
    ValidationCategory,
    ValidationFinding,
)
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import IllegalTransitionError, RunState
from vibe.research_pipeline.store import ImmutableRecordError, ResearchStore, StoreError
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

_BASE_FP = dict(
    strategy_id="orb",
    ruleset_content_sha256="a" * 64,
    parameters={"opening_range_minutes": 15},
    universe_hash="b" * 64,
    split_manifest_hash="c" * 64,
    data_snapshot_id="databento-2026-09-01",
    feature_set_version=1,
    execution_model_version=1,
    metric_calculation_version=1,
    code_commit="1234567abcdef",
    code_dirty=False,
)


def _fp(**overrides) -> RunFingerprint:
    return RunFingerprint(**{**_BASE_FP, **overrides})


def _evidence(**overrides) -> RunEvidence:
    defaults = dict(
        trade_count=3,
        trade_ledger_sha256="1" * 64,
        equity_curve_sha256="2" * 64,
        equity_points=10,
        first_session=date(2026, 1, 2),
        last_session=date(2026, 1, 9),
        starting_equity=100_000.0,
        ending_equity=101_000.0,
        gross_pnl=1_100.0,
        total_costs=100.0,
        net_pnl=1_000.0,
        max_observed_leverage=1.2,
        min_cash=50_000.0,
        retention_profile=RetentionProfile.SUMMARY,
    )
    return RunEvidence(**{**defaults, **overrides})


@pytest.fixture
def store(tmp_path: Path) -> SqliteResearchStore:
    s = SqliteResearchStore(tmp_path / "research.sqlite3")
    yield s
    s.close()


def _register(store: SqliteResearchStore, **fp_overrides) -> str:
    return store.register_run(
        fingerprint=_fp(**fp_overrides), methodology_version="v1"
    )


# -- schema / migrations -------------------------------------------------


def test_migrate_is_idempotent_and_sets_version(tmp_path: Path):
    """Re-running migrate must not re-apply DDL (which would error on CREATE)."""
    conn = sqlite3.connect(str(tmp_path / "db.sqlite3"))
    schema.configure_connection(conn)
    assert schema.migrate(conn) == schema.SCHEMA_VERSION
    # Second call is a no-op: no "table already exists" and version unchanged.
    assert schema.migrate(conn) == schema.SCHEMA_VERSION
    applied = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert applied == schema.SCHEMA_VERSION
    conn.close()


def test_foreign_keys_are_enforced(store: SqliteResearchStore):
    """FKs default OFF in SQLite; a child row for a missing run must be rejected."""
    with pytest.raises(sqlite3.IntegrityError):
        with store._conn:
            store._conn.execute(
                "INSERT INTO run_metrics "
                "(run_id, metric_key, metric_value, calculation_version, recorded_at) "
                "VALUES ('does-not-exist', 'k', 1.0, 1, '2026-01-01T00:00:00+00:00')"
            )


def test_store_satisfies_protocol(store: SqliteResearchStore):
    assert isinstance(store, ResearchStore)


# -- registration identity ----------------------------------------------


def test_register_is_idempotent_on_fingerprint(store: SqliteResearchStore):
    """Same fingerprint twice must reuse the id, not fork the run history."""
    first = _register(store)
    second = _register(store)
    assert first == second
    count = store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert count == 1


def test_distinct_fingerprints_create_distinct_runs(store: SqliteResearchStore):
    a = _register(store, strategy_id="orb")
    b = _register(store, strategy_id="orb_v2")
    assert a != b
    count = store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert count == 2


def test_run_id_is_uuid_and_alias_is_sequential(store: SqliteResearchStore):
    import uuid

    a = _register(store, strategy_id="s1")
    b = _register(store, strategy_id="s2")
    uuid.UUID(a)  # raises if not a UUID
    ra, rb = store.get_run(a), store.get_run(b)
    assert ra.display_alias == "RUN-000001"
    assert rb.display_alias == "RUN-000002"


def test_find_by_fingerprint_round_trips(store: SqliteResearchStore):
    fp = _fp(strategy_id="round")
    run_id = store.register_run(fingerprint=fp, methodology_version="v1")
    found = store.find_by_fingerprint(fp.fingerprint)
    assert found is not None
    assert found.run_id == run_id
    assert found.methodology_version == "v1"
    assert store.find_by_fingerprint("z" * 64) is None


# -- lifecycle enforcement ----------------------------------------------


def test_illegal_transition_is_rejected(store: SqliteResearchStore):
    """RUNNING -> COMPLETED skips validation; the state machine must forbid it."""
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING)
    with pytest.raises(IllegalTransitionError):
        store.transition(run_id, target=RunState.COMPLETED)


def test_transitions_recorded_in_order_with_utc(store: SqliteResearchStore):
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING, reason="start")
    store.transition(run_id, target=RunState.VALIDATING, reason="done")
    store.transition(run_id, target=RunState.COMPLETED, reason="passed")
    rows = store.get_transitions(run_id)
    states = [(r["from_state"], r["to_state"]) for r in rows]
    assert states == [
        (None, "registered"),
        ("registered", "running"),
        ("running", "validating"),
        ("validating", "completed"),
    ]
    for r in rows:
        # Parseable ISO-8601 that carries a UTC offset, never local naive time.
        from datetime import datetime

        parsed = datetime.fromisoformat(r["occurred_at"])
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def test_transition_on_terminal_run_raises_immutable(store: SqliteResearchStore):
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING)
    store.transition(run_id, target=RunState.VALIDATING)
    store.transition(run_id, target=RunState.COMPLETED)
    with pytest.raises(ImmutableRecordError):
        store.transition(run_id, target=RunState.ARCHIVED)


def test_terminal_update_blocked_by_database_trigger(store: SqliteResearchStore):
    """Prove the DB trigger, not Python, enforces immutability.

    We bypass the store API entirely and issue a raw UPDATE against a completed
    run. If only application code guarded this, the UPDATE would succeed.
    """
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING)
    store.transition(run_id, target=RunState.VALIDATING)
    store.transition(run_id, target=RunState.COMPLETED)
    with pytest.raises(sqlite3.IntegrityError):
        with store._conn:
            store._conn.execute(
                "UPDATE runs SET notes = 'tampered' WHERE run_id = ?", (run_id,)
            )


# -- attachments ---------------------------------------------------------


def test_record_metrics_stores_calculation_version(store: SqliteResearchStore):
    """A metric value must carry the version that computed it, or it is not
    comparable across runs."""
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING)
    store.record_metrics(run_id, {"sharpe": 1.4, "net_pnl": 1000.0}, calculation_version=3)
    rows = {r["metric_key"]: r for r in store.get_metrics(run_id)}
    assert rows["sharpe"]["metric_value"] == pytest.approx(1.4)
    assert rows["sharpe"]["calculation_version"] == 3
    assert rows["net_pnl"]["calculation_version"] == 3


def test_record_evidence_under_summary_round_trips(store: SqliteResearchStore):
    """Even a summary-only run stores ledger checksums so it stays verifiable."""
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING)
    ev = _evidence(retention_profile=RetentionProfile.SUMMARY)
    store.record_evidence(run_id, ev)
    got = store.get_evidence(run_id)
    assert got is not None
    assert got.trade_ledger_sha256 == "1" * 64
    assert got.equity_curve_sha256 == "2" * 64
    assert got.retention_profile is RetentionProfile.SUMMARY


def test_record_findings_appends(store: SqliteResearchStore):
    run_id = _register(store)
    store.transition(run_id, target=RunState.RUNNING)
    finding = ValidationFinding(
        code="ACC-003",
        category=ValidationCategory.ACCOUNTING,
        severity=Severity.BLOCKING,
        message="P&L does not reconcile",
    )
    store.record_findings(run_id, [finding])
    count = store._conn.execute(
        "SELECT COUNT(*) FROM run_findings WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    assert count == 1


# -- publication guardrails ---------------------------------------------


def test_enqueue_rejects_top_level_trade_payload(store: SqliteResearchStore):
    run_id = _register(store)
    with pytest.raises(StoreError):
        store.enqueue_publication(run_id, {"metrics": {}, "trades": [1, 2, 3]})


def test_enqueue_rejects_nested_trade_payload(store: SqliteResearchStore):
    """The refusal must be recursive; a nested equity_curve must not slip through."""
    run_id = _register(store)
    payload = {"summary": {"details": {"equity_curve": [1.0, 2.0]}}}
    with pytest.raises(StoreError):
        store.enqueue_publication(run_id, payload)
    # And nothing was written.
    assert store.get_outbox(run_id) is None


def test_enqueue_is_idempotent_per_run(store: SqliteResearchStore):
    run_id = _register(store)
    store.enqueue_publication(run_id, {"metric": 1})
    store.enqueue_publication(run_id, {"metric": 2})
    count = store._conn.execute(
        "SELECT COUNT(*) FROM publication_outbox WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    assert count == 1


# -- queries -------------------------------------------------------------


def test_query_runs_filters_by_state_and_paginates(store: SqliteResearchStore):
    ids = [_register(store, strategy_id=f"s{i}") for i in range(5)]
    # Move the first two to RUNNING.
    for run_id in ids[:2]:
        store.transition(run_id, target=RunState.RUNNING)

    running = store.query_runs(state=RunState.RUNNING)
    assert {r.run_id for r in running} == set(ids[:2])

    registered = store.query_runs(state=RunState.REGISTERED)
    assert len(registered) == 3

    page1 = store.query_runs(limit=2, offset=0)
    page2 = store.query_runs(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {r.run_id for r in page1}.isdisjoint({r.run_id for r in page2})


def test_two_connections_see_same_committed_data(tmp_path: Path):
    """Proves WAL + commit: a second connection reads the first's committed run."""
    path = tmp_path / "shared.sqlite3"
    writer = SqliteResearchStore(path)
    run_id = writer.register_run(fingerprint=_fp(strategy_id="shared"), methodology_version="v1")

    reader = SqliteResearchStore(path)
    seen = reader.get_run(run_id)
    assert seen is not None
    assert seen.run_id == run_id
    writer.close()
    reader.close()
