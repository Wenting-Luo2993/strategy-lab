from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.methodology import FrozenStudyDefinition
from vibe.research_pipeline.reliability import ReliabilityPolicy
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.store import ImmutableRecordError


def _fingerprint(seed: int) -> RunFingerprint:
    return RunFingerprint(
        strategy_id="orb",
        ruleset_content_sha256="a" * 64,
        universe_hash="b" * 64,
        split_manifest_hash="c" * 64,
        data_snapshot_id="dataset-1",
        feature_set_version=1,
        execution_model_version=2,
        metric_calculation_version=2,
        code_commit="d" * 40,
        code_dirty=False,
        random_seed=seed,
    )


def _definition() -> FrozenStudyDefinition:
    return FrozenStudyDefinition.freeze(
        strategy_id="orb",
        ruleset_identity={"sha256": "rules"},
        universe_identity={"symbols": ["QQQ"]},
        dataset_ids=("dataset-1",),
        parameter_axes=(),
        split_policy={"kind": "nested"},
        warmup_policy={"sessions": 20},
        execution_model_version=2,
        metric_calculation_version=2,
        leakage_requirements=("purged",),
        reliability_policy=ReliabilityPolicy(),
        final_holdout_lock_hash="sha256:holdout",
    )


def _run_with_detail(store: SqliteResearchStore, seed: int) -> str:
    run_id = store.register_run(
        fingerprint=_fingerprint(seed),
        methodology_version="test/v1",
        segment_role=SegmentRole.TEST,
    )
    with store._conn:
        store._conn.execute(
            """INSERT INTO run_evidence
               (run_id, trade_count, trade_ledger_sha256, equity_curve_sha256,
                equity_points, retention_profile, payload_json, recorded_at)
               VALUES (?, 1, ?, ?, 1, 'full', '{}', '2026-10-17T00:00:00Z')""",
            (run_id, "a" * 64, "b" * 64),
        )
        store._conn.execute(
            """INSERT INTO run_trades
               (run_id, sequence, symbol, side, quantity, entry_price,
                entry_time, recorded_at)
               VALUES (?, 0, 'QQQ', 'long', 1, 100, '2026-01-01T14:30:00Z',
                       '2026-10-17T00:00:00Z')""",
            (run_id,),
        )
        store._conn.execute(
            """INSERT INTO run_equity_points
               (run_id, sequence, observed_at, equity, recorded_at)
               VALUES (?, 0, '2026-01-01T14:30:00Z', 100000,
                       '2026-10-17T00:00:00Z')""",
            (run_id,),
        )
    return run_id


def test_ordinary_detail_is_pruned_only_by_explicit_api(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _run_with_detail(store, 1)

    assert store._conn.execute(
        "SELECT COUNT(*) FROM run_trades WHERE run_id = ?", (run_id,)
    ).fetchone()[0] == 1
    with pytest.raises(ValueError, match="requester and reason"):
        store.prune_run_detail(run_id, requested_by="", reason="short")

    deleted = store.prune_run_detail(
        run_id,
        requested_by="Bloom",
        reason="Exploratory detail is no longer required",
    )

    assert deleted == {"trades": 1, "equity_points": 1}
    assert store._conn.execute(
        "SELECT COUNT(*) FROM run_evidence WHERE run_id = ?", (run_id,)
    ).fetchone()[0] == 1


def test_frozen_study_detail_is_protected_indefinitely(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _run_with_detail(store, 2)
    experiment_id = store.start_experiment(
        "Protected study", {}, reliability_policy=ReliabilityPolicy()
    )
    store.link_experiment_run(
        experiment_id, 0, "candidate-1", SegmentRole.TEST, run_id
    )
    store.freeze_study(experiment_id, _definition())

    with pytest.raises(ImmutableRecordError, match="retained indefinitely"):
        store.prune_run_detail(
            run_id,
            requested_by="Bloom",
            reason="Attempted protected evidence cleanup",
        )


def test_concurrent_freeze_wins_before_prune_checks_protection(tmp_path):
    path = tmp_path / "research.sqlite3"
    freezer = SqliteResearchStore(path)
    run_id = _run_with_detail(freezer, 3)
    definition = _definition()
    experiment_id = freezer.start_experiment(
        "Race protected study",
        {},
        reliability_policy=definition.reliability_policy,
    )
    freezer.link_experiment_run(
        experiment_id, 0, "candidate-1", SegmentRole.TEST, run_id
    )

    from vibe.research_pipeline.storage import schema

    schema.begin_immediate(freezer._conn)
    freezer._conn.execute(
        """INSERT INTO frozen_study_definitions
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            experiment_id,
            definition.definition_hash,
            definition.reliability_policy.policy_hash,
            definition.final_holdout_lock_hash,
            definition.frozen_at.isoformat(),
            definition.frozen_by,
            definition.model_dump_json(),
        ),
    )

    def prune():
        store = SqliteResearchStore(path)
        try:
            store.prune_run_detail(
                run_id,
                requested_by="Bloom",
                reason="Concurrent cleanup must lose to freeze",
            )
        except ImmutableRecordError:
            return "protected"
        finally:
            store.close()
        return "deleted"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(prune)
        freezer._conn.commit()
        assert future.result(timeout=10) == "protected"

    assert freezer._conn.execute(
        "SELECT COUNT(*) FROM run_trades WHERE run_id = ?", (run_id,)
    ).fetchone()[0] == 1


def test_preprotected_final_run_cannot_be_pruned_before_verdict_link(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _run_with_detail(store, 4)
    store.protect_run_detail(
        run_id, reason="final holdout evidence is retained indefinitely"
    )

    with pytest.raises(ImmutableRecordError, match="retained indefinitely"):
        store.prune_run_detail(
            run_id,
            requested_by="Bloom",
            reason="Concurrent cleanup before final verdict link",
        )
    with pytest.raises(Exception, match="retained indefinitely"):
        store._conn.execute("DELETE FROM run_trades WHERE run_id = ?", (run_id,))
