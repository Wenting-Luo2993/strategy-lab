"""P11 contracts for the SQLite-authoritative research registry."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest
import yaml

from vibe.backtester.core.execution_realism import (
    EXECUTION_MODEL_VERSION,
    ExecutionRealismConfig,
)
from vibe.backtester.integration.sweep_tracker import (
    ParameterSweepExperimentTracker,
    SweepResultExperimentLinker,
)
from vibe.research_journal.artifact_tracker import ArtifactTracker
from vibe.research_journal.models import (
    ExecutionMetadata,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
)
from vibe.research_journal.persistence import save_experiment, save_hypothesis
from vibe.research_journal.query import ExperimentQuery
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_pipeline.holdout import (
    AcceptanceRule,
    HoldoutLock,
    UnlockToken,
    record_access,
)
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.store import ResearchStore
from vibe.research_pipeline.validation import (
    ValidationReport,
    ValidationScope,
)
from vibe.research_pipeline.contracts import ValidationCategory
from vibe.research_pipeline.store import ValidationRequiredError


def _fingerprint(strategy_id: str) -> RunFingerprint:
    return RunFingerprint(
        strategy_id=strategy_id,
        ruleset_content_sha256="a" * 64,
        parameters={"opening_range_minutes": 15},
        universe_hash="b" * 64,
        split_manifest_hash="c" * 64,
        data_snapshot_id="fixture",
        feature_set_version=1,
        execution_model_version=EXECUTION_MODEL_VERSION,
        metric_calculation_version=2,
        code_commit="1234567abcdef",
        code_dirty=False,
    )


def _promotion_report() -> ValidationReport:
    return ValidationReport(
        profile_name="promotion",
        scope=ValidationScope.PROMOTION,
        target_state=RunState.COMPLETED,
        findings=(),
        checked_categories=tuple(ValidationCategory),
        registry_hash_at_execution="a" * 64,
        current_registry_hash="a" * 64,
        leakage_passed=True,
    )


def _holdout_files(tmp_path: Path) -> tuple[Path, Path, HoldoutLock]:
    lock = HoldoutLock(
        dev_end=date(2024, 12, 31),
        oos_start=date(2025, 1, 1),
        oos_end=date(2026, 4, 27),
        symbols=("QQQ",),
        declared_at=date(2026, 9, 9),
        declared_by="test",
        acceptance=AcceptanceRule(
            metric="expectancy_r",
            comparison=">=",
            threshold=0.1,
            min_trades=10,
        ),
    )
    config = tmp_path / "holdout.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": lock.version,
                "dev_end": lock.dev_end.isoformat(),
                "oos_start": lock.oos_start.isoformat(),
                "oos_end": lock.oos_end.isoformat(),
                "symbols": list(lock.symbols),
                "declared_at": lock.declared_at.isoformat(),
                "declared_by": lock.declared_by,
                "acceptance": {
                    "metric": lock.acceptance.metric,
                    "comparison": lock.acceptance.comparison,
                    "threshold": lock.acceptance.threshold,
                    "min_trades": lock.acceptance.min_trades,
                },
            }
        ),
        encoding="utf-8",
    )
    return config, tmp_path / "access.jsonl", lock


def test_schema_is_forward_only_and_store_protocol_is_extended(tmp_path: Path):
    path = tmp_path / "research.sqlite3"
    conn = sqlite3.connect(path)
    schema.configure_connection(conn)
    assert schema.migrate(conn) == schema.SCHEMA_VERSION
    assert [row[0] for row in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    )    ] == list(range(1, schema.SCHEMA_VERSION + 1))
    conn.close()

    store = SqliteResearchStore(path)
    assert isinstance(store, ResearchStore)
    assert store.allocate_registry_id(
        record_type="experiment", prefix="EXP"
    ) == "EXP-001"
    store.close()


def test_failed_migration_rolls_back_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "research.sqlite3"
    conn = sqlite3.connect(path)
    schema.configure_connection(conn)
    schema.migrate(conn)
    next_version = schema.SCHEMA_VERSION + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", next_version)
    monkeypatch.setattr(
        schema,
        "_MIGRATIONS",
        [
            *schema._MIGRATIONS,
            (
                next_version,
                "CREATE TABLE migration_must_rollback (id INTEGER); "
                "THIS IS INVALID SQL;",
            ),
        ],
    )

    with pytest.raises(sqlite3.DatabaseError):
        schema.migrate(conn)

    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'table' AND name = 'migration_must_rollback'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
        (next_version,),
    ).fetchone()[0] == 0
    conn.close()


def test_concurrent_fresh_migration_serializes_initialization(tmp_path: Path):
    path = tmp_path / "concurrent-fresh.sqlite3"
    barrier = Barrier(4)

    def initialize(_: int) -> int:
        conn = sqlite3.connect(path, timeout=0.05)
        schema.configure_connection(conn)
        barrier.wait(timeout=10)
        try:
            return schema.migrate(conn)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(initialize, range(4)))

    assert versions == [schema.SCHEMA_VERSION] * 4
    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0] == schema.SCHEMA_VERSION
    conn.close()


@pytest.mark.parametrize("starting_version", [1, 2, 3])
def test_concurrent_upgrade_serializes_version_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, starting_version: int
):
    path = tmp_path / f"upgrade-v{starting_version}.sqlite3"
    conn = sqlite3.connect(path)
    schema.configure_connection(conn)
    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(
            schema,
            "_MIGRATIONS",
            schema._MIGRATIONS[:starting_version],
        )
        migration_patch.setattr(schema, "SCHEMA_VERSION", starting_version)
        assert schema.migrate(conn) == starting_version
    conn.close()
    barrier = Barrier(3)

    def upgrade(_: int) -> int:
        worker = sqlite3.connect(path, timeout=0.05)
        schema.configure_connection(worker)
        barrier.wait(timeout=10)
        try:
            return schema.migrate(worker)
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        versions = list(pool.map(upgrade, range(3)))

    assert versions == [schema.SCHEMA_VERSION] * 3
    conn = sqlite3.connect(path)
    assert [
        row[0]
        for row in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ] == list(range(1, schema.SCHEMA_VERSION + 1))
    conn.close()


def test_registry_query_lineage_notes_artifacts_and_sweep_write_no_yaml(
    tmp_path: Path,
):
    root = tmp_path / "research"
    artifact_file = root / "artifacts" / "result.txt"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_text("result", encoding="utf-8")

    with ResearchRegistry(root) as registry:
        hypothesis = registry.create_hypothesis(
            "Store authority",
            "SQLite is the only normal-operation registry authority.",
            tags=["p11"],
        )
        tracker = ParameterSweepExperimentTracker(
            registry=registry, hypothesis_id=hypothesis.id
        )
        parent_id = tracker.create_parent_experiment(
            "ORBStrategy", "1.0", {"base": 1}, {"symbol": "QQQ"}
        )
        child_id = tracker.create_variation_experiment(
            1, "ORBStrategy", "1.0", {"orb_minutes": 10}, {"symbol": "QQQ"}
        )
        tracker.complete_variation_experiment(
            child_id, [], {"expectancy_r": 0.2}, 1, "selected"
        )
        SweepResultExperimentLinker(registry).link_sweep_results(
            parent_id,
            __import__("pandas").DataFrame({"score": [1.0]}),
        )
        artifact = ArtifactTracker(registry).register_artifact(
            child_id, artifact_file, "text"
        )

        assert ExperimentQuery(registry).by_parameter(
            "orb_minutes", 10
        ).execute()[0].id == child_id
        assert registry.get_lineage_graph().get_parent(child_id) == parent_id
        assert registry.list_research_notes()[0].related_experiment_id == parent_id
        assert registry.get_artifact(artifact.id).checksum == artifact.checksum
        stored = registry.get_experiment(child_id)
        assert stored.methodology_version == "research-store-v1"
        assert stored.execution_model_version == EXECUTION_MODEL_VERSION
        assert stored.execution_identity["entry_fill_policy"] == "at_next_bar_open"

    assert not list(root.rglob("*.yaml"))
    assert not list(root.rglob("NOTE-*.md"))


def test_legacy_yaml_is_imported_once_then_sqlite_is_authoritative(tmp_path: Path):
    root = tmp_path / "research"
    now = datetime.now(timezone.utc)
    save_hypothesis(
        Hypothesis(
            id="HYP-007",
            title="Legacy",
            rationale="Legacy compatibility import remains explicitly bounded.",
            status=HypothesisStatus.PROPOSED,
            created_at=now,
            updated_at=now,
        ),
        root,
    )
    save_experiment(
        Experiment(
            id="EXP-009",
            strategy_name="ORBStrategy",
            strategy_version="0.9",
            parameters={},
            dataset_config={},
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="legacy",
                git_dirty=False,
                executed_at=now,
                python_version="3.12",
            ),
            status=ExperimentStatus.COMPLETED,
            results_summary={"expectancy_r": 0.1},
            conclusion="legacy",
            created_at=now,
            completed_at=now,
        ),
        root,
    )

    with ResearchRegistry(root) as registry:
        imported = registry.get_experiment("EXP-009")
        assert imported.methodology_version == "legacy-uncontrolled"
        assert imported.execution_model_version is None
        created = registry.create_experiment(
            "ORBStrategy", "1.0", {}, {}, hypothesis_id="HYP-007"
        )
        assert created.id == "EXP-010"

    # Editing the source after import cannot create a second authority.
    source = root / "hypotheses" / "HYP-007.yaml"
    source.write_text(source.read_text(encoding="utf-8").replace("Legacy", "Changed"))
    with ResearchRegistry(root) as registry:
        assert registry.get_hypothesis("HYP-007").title == "Legacy"
        audit = registry._sqlite_store._conn.execute(
            "SELECT status, record_count FROM registry_import_audit"
        ).fetchone()
        assert audit["status"] == "completed"
        assert audit["record_count"] == 2


def test_concurrent_legacy_initialization_has_one_verified_audit(tmp_path: Path):
    root = tmp_path / "research"
    now = datetime.now(timezone.utc)
    save_hypothesis(
        Hypothesis(
            id="HYP-001",
            title="Concurrent import",
            rationale="Concurrent startup must converge on one verified import.",
            status=HypothesisStatus.PROPOSED,
            created_at=now,
            updated_at=now,
        ),
        root,
    )
    barrier = Barrier(3)

    def initialize(_: int) -> str:
        barrier.wait(timeout=10)
        with ResearchRegistry(root) as registry:
            return registry.get_hypothesis("HYP-001").title

    with ThreadPoolExecutor(max_workers=3) as pool:
        titles = list(pool.map(initialize, range(3)))

    assert titles == ["Concurrent import"] * 3
    db_path = root.parent / f".{root.name}-research.sqlite3"
    conn = sqlite3.connect(db_path)
    audit = conn.execute(
        "SELECT status, canonical_tree_sha256, record_count "
        "FROM registry_import_audit"
    ).fetchone()
    assert audit[0] == "completed"
    assert audit[1]
    assert audit[2] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM registry_import_audit"
    ).fetchone()[0] == 1
    conn.close()


def test_concurrent_registry_id_allocation_is_unique(tmp_path: Path):
    root = tmp_path / "research"
    seed = ResearchRegistry(root)
    db_path = seed._sqlite_db_path
    seed.close()

    def create(index: int) -> str:
        store = SqliteResearchStore(db_path)
        registry = ResearchRegistry(root, sqlite_store=store, import_legacy_yaml=False)
        try:
            return registry.create_hypothesis(
                f"Hypothesis {index}",
                "Concurrency must never allocate duplicate display IDs.",
            ).id
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(8)))
    assert sorted(ids) == [f"HYP-{index:03d}" for index in range(1, 9)]


def test_second_holdout_touch_blocks_promotion_for_entire_lineage(tmp_path: Path):
    config, access_log, lock = _holdout_files(tmp_path)
    store = SqliteResearchStore(
        tmp_path / "research.sqlite3",
        holdout_config_path=config,
        holdout_access_log_path=access_log,
    )
    first = store.register_run(
        fingerprint=_fingerprint("orb-a"),
        methodology_version="v1",
        lineage_id="lineage-orb",
    )
    second = store.register_run(
        fingerprint=_fingerprint("orb-b"),
        methodology_version="v1",
        lineage_id="lineage-orb",
    )
    for run_id in (first, second):
        token = store.start_run(run_id, owner="pytest", lease_seconds=60)
        store.transition(
            run_id, target=RunState.VALIDATING, lease_token=token
        )
        record_access(
            lock=lock,
            token=UnlockToken(
                reason="final frozen methodology evaluation",
                requested_by="pytest",
                run_id=run_id,
            ),
            symbol="QQQ",
            start=lock.oos_start,
            end=lock.oos_end,
            path=access_log,
        )

    store.finalize_validation(second, _promotion_report())

    assert store.get_run(second).state is RunState.VALIDATION_FAILED
    findings = store.get_findings(second)
    assert findings[-1].code == "OOS-001"
    assert findings[-1].observed == 2
    store.close()


def test_completed_report_cannot_bypass_p6_evidence(tmp_path: Path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = store.register_run(
        fingerprint=_fingerprint("orb-invalid-report"),
        methodology_version="v1",
    )
    token = store.start_run(run_id, owner="pytest", lease_seconds=60)
    store.transition(run_id, target=RunState.VALIDATING, lease_token=token)
    incomplete = _promotion_report().model_copy(
        update={
            "checked_categories": (),
            "leakage_passed": False,
            "current_registry_hash": "b" * 64,
        }
    )

    with pytest.raises(ValidationRequiredError):
        store.finalize_validation(run_id, incomplete)

    assert store.get_run(run_id).state is RunState.VALIDATING
    store.close()


def test_sweep_tracker_stamps_custom_execution_identity(tmp_path: Path):
    custom = ExecutionRealismConfig.legacy()
    with ResearchRegistry(tmp_path / "research") as registry:
        tracker = ParameterSweepExperimentTracker(
            registry=registry,
            execution_realism=custom,
        )
        parent_id = tracker.create_parent_experiment(
            "ORBStrategy", "1.0", {}, {"symbol": "QQQ"}
        )
        child_id = tracker.create_variation_experiment(
            1, "ORBStrategy", "1.0", {}, {"symbol": "QQQ"}
        )

        assert registry.get_experiment(parent_id).execution_identity == custom.identity()
        assert registry.get_experiment(child_id).execution_identity == custom.identity()
