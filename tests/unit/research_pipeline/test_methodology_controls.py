from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from vibe.backtester.core.integrity import (
    EngineIntegrityCategory,
    EngineIntegrityReport,
    EngineIntegrityStatus,
)
from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.identity import RunFingerprint
from vibe.research_pipeline.methodology import (
    FinalHoldoutAuthorization,
    FrozenStudyDefinition,
)
from vibe.research_pipeline.reliability import ReliabilityPolicy
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.store import ImmutableRecordError, ValidationRequiredError


def _definition() -> FrozenStudyDefinition:
    return FrozenStudyDefinition.freeze(
        strategy_id="orb",
        ruleset_identity={"sha256": "rules"},
        universe_identity={"symbols": ["QQQ"]},
        dataset_ids=("dataset-1",),
        parameter_axes=({"name": "stop", "values": [1.0, 1.5]},),
        split_policy={"kind": "nested"},
        warmup_policy={"sessions": 20},
        execution_model_version=2,
        metric_calculation_version=2,
        leakage_requirements=("purged",),
        reliability_policy=ReliabilityPolicy(),
        final_holdout_lock_hash="sha256:holdout",
        frozen_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )


def _authorization() -> FinalHoldoutAuthorization:
    return FinalHoldoutAuthorization(
        token="0123456789abcdef",
        reason="Execute the one final locked holdout",
    )


def _run(store: SqliteResearchStore) -> str:
    return store.register_run(
        fingerprint=RunFingerprint(
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
        ),
        methodology_version="final-holdout/test",
        segment_role=SegmentRole.FINAL_OOS,
    )


def test_freeze_and_holdout_authority_is_bloom():
    assert _definition().frozen_by == "Bloom"
    assert _authorization().requested_by == "Bloom"
    with pytest.raises(ValidationError, match="Bloom"):
        FrozenStudyDefinition.model_validate(
            {**_definition().model_dump(), "frozen_by": "Other"}
        )
    with pytest.raises(ValidationError, match="Bloom"):
        FinalHoldoutAuthorization(
            token="0123456789abcdef",
            reason="Execute the one final locked holdout",
            requested_by="Other",
        )


def test_unfrozen_holdout_is_refused_and_frozen_verdict_is_exactly_once(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    policy = ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "Methodology controls", {}, reliability_policy=policy
    )

    with pytest.raises(ValidationRequiredError, match="frozen"):
        store.authorize_final_holdout(experiment_id, _authorization())

    definition_hash = store.freeze_study(experiment_id, _definition())
    assert store.get_frozen_study(experiment_id).definition_hash == definition_hash
    with pytest.raises(ImmutableRecordError, match="already frozen"):
        store.freeze_study(experiment_id, _definition())

    store.authorize_final_holdout(experiment_id, _authorization())
    claim = store.claim_final_holdout(experiment_id, _authorization().token)
    store.record_final_holdout(
        experiment_id,
        claim,
        verdict="passed",
        payload={"accepted": True},
        run_id=_run(store),
        verified_identities={"definition_hash": definition_hash},
    )
    for table in (
        "final_holdout_executions",
        "frozen_study_definitions",
        "final_holdout_authorizations",
    ):
        with pytest.raises(Exception, match="immutable"):
            store._conn.execute(
                f"DELETE FROM {table} WHERE experiment_id = ?",
                (experiment_id,),
            )
        store._conn.rollback()
    with pytest.raises(ImmutableRecordError, match="already consumed"):
        store.claim_final_holdout(experiment_id, _authorization().token)


def test_final_holdout_claim_is_atomic_across_two_connections(tmp_path):
    path = tmp_path / "research.sqlite3"
    setup = SqliteResearchStore(path)
    experiment_id = setup.start_experiment(
        "Concurrent claim", {}, reliability_policy=ReliabilityPolicy()
    )
    setup.freeze_study(experiment_id, _definition())
    setup.authorize_final_holdout(experiment_id, _authorization())
    setup.close()

    def claim():
        store = SqliteResearchStore(path)
        try:
            return store.claim_final_holdout(
                experiment_id, _authorization().token
            ).claim_id
        except ImmutableRecordError:
            return None
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))

    assert sum(result is not None for result in results) == 1


def test_authorization_cannot_be_deleted_and_reissued(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = store.start_experiment(
        "Immutable authorization", {}, reliability_policy=ReliabilityPolicy()
    )
    store.freeze_study(experiment_id, _definition())
    store.authorize_final_holdout(experiment_id, _authorization())

    with pytest.raises(Exception, match="authorization is immutable"):
        store._conn.execute(
            "DELETE FROM final_holdout_authorizations WHERE experiment_id = ?",
            (experiment_id,),
        )
    with pytest.raises(ImmutableRecordError, match="already exists"):
        store.authorize_final_holdout(experiment_id, _authorization())


def test_freeze_rejects_policy_hash_mismatch(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = store.start_experiment(
        "Policy mismatch",
        {},
        reliability_policy=ReliabilityPolicy(min_stitched_oos_trades=10),
    )
    with pytest.raises(ImmutableRecordError, match="differs"):
        store.freeze_study(experiment_id, _definition())


def test_engine_integrity_record_cannot_be_deleted(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    run_id = _run(store)
    store.record_engine_integrity(
        run_id,
        EngineIntegrityReport(
            status=EngineIntegrityStatus.PASS,
            engine_version=6,
            metric_calculation_version=2,
            checked_categories=tuple(EngineIntegrityCategory),
            findings=(),
        ),
    )
    with pytest.raises(Exception, match="immutable"):
        store._conn.execute(
            "DELETE FROM engine_integrity_reports WHERE run_id = ?", (run_id,)
        )
