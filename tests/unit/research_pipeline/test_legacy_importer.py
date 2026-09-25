"""F11 and compatibility tests for the P7 legacy-tree importer."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from vibe.research_journal.registry import ResearchRegistry
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.legacy_importer import (
    LEGACY_METHODOLOGY_VERSION,
    LegacyParityError,
    import_legacy_tree,
    load_legacy_records,
    verify_legacy_import,
)
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LEGACY_ROOT = REPOSITORY_ROOT / "research"

_FINGERPRINT = dict(
    strategy_id="concurrent-orb",
    ruleset_content_sha256="a" * 64,
    parameters={"opening_range_minutes": 15},
    universe_hash="b" * 64,
    split_manifest_hash="c" * 64,
    data_snapshot_id="fixture",
    feature_set_version=1,
    execution_model_version=1,
    metric_calculation_version=1,
    code_commit="1234567abcdef",
    code_dirty=False,
)


def test_forward_only_upgrade_from_schema_v1(tmp_path: Path, monkeypatch):
    path = tmp_path / "upgrade.sqlite3"
    conn = sqlite3.connect(path)
    schema.configure_connection(conn)
    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(schema, "_MIGRATIONS", [(1, schema._MIGRATION_1)])
        assert schema.migrate(conn) == 1
    assert schema.migrate(conn) == schema.SCHEMA_VERSION == 3
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "runs" in tables
    assert "registry_records" in tables
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
    conn.close()


def test_terminal_run_delete_is_blocked_by_database_trigger(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "terminal.sqlite3")
    fingerprint = RunFingerprint(**_FINGERPRINT)
    run_id = store.register_run(fingerprint=fingerprint, methodology_version="v1")
    from vibe.research_pipeline.lifecycle import RunState

    store.transition(run_id, target=RunState.RUNNING)
    store.transition(run_id, target=RunState.VALIDATING)
    store.transition(run_id, target=RunState.COMPLETED)
    with pytest.raises(sqlite3.IntegrityError, match="terminal"):
        with store._conn:
            store._conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    store.close()


def test_f11_imports_complete_repository_tree_with_exact_parity(tmp_path: Path):
    """F11: every checked-in legacy record survives import and verification."""
    conn = sqlite3.connect(tmp_path / "f11.sqlite3")
    verification = import_legacy_tree(
        LEGACY_ROOT, conn, repository_root=REPOSITORY_ROOT
    )

    assert verification.source_counts == {
        "artifact": 16,
        "experiment": 73,
        "hypothesis": 4,
        "note": 5,
    }
    assert verification.database_counts == verification.source_counts
    assert verification.record_count == 98
    assert verification.relationship_count == 158
    assert verification.terminal_experiment_count == 73
    assert (
        verification.canonical_tree_sha256
        == "1c137756c9a83d5351270005d190bdb060fa51ff444692f83be6a0d09d462d60"
    )
    assert verify_legacy_import(LEGACY_ROOT, conn) == verification
    assert conn.execute(
        "SELECT COUNT(*) FROM registry_relationships WHERE resolved = 0"
    ).fetchone()[0] == 0

    methodologies = conn.execute(
        "SELECT DISTINCT methodology_version FROM registry_records"
    ).fetchall()
    assert [row[0] for row in methodologies] == [LEGACY_METHODOLOGY_VERSION]
    assert conn.execute(
        "SELECT git_dirty FROM registry_records "
        "WHERE record_type = 'experiment' AND legacy_id = 'EXP-073'"
    ).fetchone()[0] == 1

    # The original absolute locations remain auditable while paths beneath an
    # older checkout of this repository are normalized for the current clone.
    locations = conn.execute(
        "SELECT original_location, repository_path, external_uri "
        "FROM registry_artifact_locations "
        "WHERE record_type = 'experiment' AND legacy_id = 'EXP-073' "
        "ORDER BY ordinal"
    ).fetchall()
    assert len(locations) == 2
    assert locations[0][0].startswith(
        "C:/Users/wentingluo/OneDrive - Microsoft/Development/strategy-lab/"
    )
    assert locations[0][1].startswith("reports/optimization/orb_reality_check_exp073/")
    assert locations[0][2] is None

    # Inflated capital-denominated values are retained as evidence but cannot
    # be consumed as an effective baseline.
    total_pnl = conn.execute(
        "SELECT original_value, effective_value, baseline_status "
        "FROM legacy_metric_values "
        "WHERE experiment_id = 'EXP-073' AND metric_key = 'total_pnl'"
    ).fetchone()
    assert total_pnl[0] == pytest.approx(-45088.568178426256)
    assert total_pnl[1] is None
    assert total_pnl[2] == "requires-rebaseline"
    expectancy = conn.execute(
        "SELECT original_value, effective_value, baseline_status "
        "FROM legacy_metric_values "
        "WHERE experiment_id = 'EXP-073' AND metric_key = 'expectancy_r'"
    ).fetchone()
    assert expectancy[1] == expectancy[0]
    assert expectancy[2] == "legacy-uncontrolled"
    conn.close()


def test_import_is_idempotent_and_detects_source_hash_drift(
    tmp_path: Path,
):
    research_root = tmp_path / "research"
    hypotheses = research_root / "hypotheses"
    hypotheses.mkdir(parents=True)
    source = hypotheses / "HYP-001.yaml"
    source.write_text(
        "id: HYP-001\ntitle: A\nrationale: long enough rationale\n"
        "status: proposed\ntags: []\n"
        "created_at: '2026-01-01T00:00:00Z'\n"
        "updated_at: '2026-01-01T00:00:00Z'\n",
        encoding="utf-8",
    )
    conn = sqlite3.connect(tmp_path / "idempotent.sqlite3")
    first = import_legacy_tree(research_root, conn, repository_root=tmp_path)
    second = import_legacy_tree(research_root, conn, repository_root=tmp_path)
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM registry_records").fetchone()[0] == 1

    source.write_text(source.read_text(encoding="utf-8").replace("title: A", "title: B"))
    with pytest.raises(LegacyParityError, match="differs"):
        import_legacy_tree(research_root, conn, repository_root=tmp_path)
    conn.close()


def test_dual_write_tracks_registry_creation_and_completion(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "dual.sqlite3")
    registry = ResearchRegistry(tmp_path / "research", sqlite_store=store)
    experiment = registry.create_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.0.0",
        parameters={"orb_minutes": 5},
        dataset_config={"symbol": "QQQ"},
    )
    registered = store._conn.execute(
        "SELECT status, methodology_version FROM registry_records "
        "WHERE record_type = 'experiment' AND legacy_id = ?",
        (experiment.id,),
    ).fetchone()
    assert tuple(registered) == ("registered", LEGACY_METHODOLOGY_VERSION)

    registry.complete_experiment(
        experiment.id, {"total_pnl": 123.0, "expectancy_r": 0.2}, "done"
    )
    completed = store._conn.execute(
        "SELECT status FROM registry_records "
        "WHERE record_type = 'experiment' AND legacy_id = ?",
        (experiment.id,),
    ).fetchone()
    assert completed[0] == "completed"
    metric = store._conn.execute(
        "SELECT effective_value, baseline_status FROM legacy_metric_values "
        "WHERE experiment_id = ? AND metric_key = 'total_pnl'",
        (experiment.id,),
    ).fetchone()
    assert tuple(metric) == (None, "requires-rebaseline")

    with pytest.raises(sqlite3.IntegrityError, match="terminal"):
        with store._conn:
            store._conn.execute(
                "UPDATE registry_records SET status = 'running' "
                "WHERE record_type = 'experiment' AND legacy_id = ?",
                (experiment.id,),
            )
    store.close()


def test_failed_completion_dual_write_restores_registered_yaml(
    tmp_path: Path, monkeypatch
):
    store = SqliteResearchStore(tmp_path / "rollback.sqlite3")
    registry = ResearchRegistry(tmp_path / "research", sqlite_store=store)
    experiment = registry.create_experiment(
        strategy_name="ORBStrategy",
        strategy_version="1.0.0",
        parameters={},
        dataset_config={},
    )
    filepath = registry.research_root / "experiments" / f"{experiment.id}.yaml"
    before = filepath.read_bytes()

    def fail_write(**_kwargs):
        raise RuntimeError("forced dual-write failure")

    monkeypatch.setattr(store, "upsert_registry_record", fail_write)
    with pytest.raises(RuntimeError, match="forced dual-write failure"):
        registry.complete_experiment(experiment.id, {"total_pnl": 1.0}, "done")

    assert filepath.read_bytes() == before
    assert registry.get_experiment(experiment.id).status.value == "registered"
    assert store._conn.execute(
        "SELECT status FROM registry_records "
        "WHERE record_type = 'experiment' AND legacy_id = ?",
        (experiment.id,),
    ).fetchone()[0] == "registered"
    store.close()


def test_same_fingerprint_concurrent_writers_converge_on_one_run(tmp_path: Path):
    path = tmp_path / "concurrent.sqlite3"
    seed = SqliteResearchStore(path)
    seed.close()
    barrier = Barrier(2)

    def register() -> str:
        store = SqliteResearchStore(path)
        barrier.wait(timeout=10)
        try:
            return store.register_run(
                fingerprint=RunFingerprint(**_FINGERPRINT),
                methodology_version="v1",
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(lambda _: register(), range(2)))

    assert run_ids[0] == run_ids[1]
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    conn.close()


def test_load_legacy_records_is_deterministic():
    first = load_legacy_records(LEGACY_ROOT)
    second = load_legacy_records(LEGACY_ROOT)
    assert [
        (record.record_type, record.legacy_id, record.canonical_sha256)
        for record in first
    ] == [
        (record.record_type, record.legacy_id, record.canonical_sha256)
        for record in second
    ]
