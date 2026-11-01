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
from vibe.research_pipeline.reliability import (
    ReliabilityPolicy,
    ReliabilityVerdict,
    StudyReliabilityReport,
)
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


def _report(policy: ReliabilityPolicy, verdict: ReliabilityVerdict):
    return StudyReliabilityReport(
        verdict=verdict,
        policy_version=policy.version,
        policy_hash=policy.policy_hash,
        engine_integrity_complete=True,
        procedure_state="completed",
        stitched_oos_summary={},
        fold_stability_summary={},
        robustness_summary={},
        holdout_summary={},
        statistical_confidence={},
        verdict_reason=f"Test verdict: {verdict.value}",
        criteria=(),
    )


def _completed_experiment(
    store: SqliteResearchStore,
    *,
    verdict: ReliabilityVerdict = ReliabilityVerdict.RELIABLE,
    policy: ReliabilityPolicy | None = None,
    freeze: bool = True,
) -> str:
    policy = policy or ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "Methodology controls", {}, reliability_policy=policy
    )
    store.finish_experiment(experiment_id, "completed", {})
    store.record_study_reliability(
        experiment_id,
        policy,
        _report(policy, verdict),
    )
    if freeze:
        store.freeze_study(experiment_id, _definition())
    return experiment_id


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
    experiment_id = _completed_experiment(store, policy=policy, freeze=False)

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
    experiment_id = _completed_experiment(setup)
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
    experiment_id = _completed_experiment(store)
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


@pytest.mark.parametrize(
    "verdict",
    [
        ReliabilityVerdict.REJECTED,
        ReliabilityVerdict.INCONCLUSIVE,
        ReliabilityVerdict.REVIEW_REQUIRED,
    ],
)
def test_non_reliable_study_cannot_authorize_final_holdout(tmp_path, verdict):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = _completed_experiment(store, verdict=verdict)

    with pytest.raises(ValidationRequiredError, match=verdict.value):
        store.authorize_final_holdout(experiment_id, _authorization())


def test_missing_reliability_report_cannot_authorize_final_holdout(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    policy = ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "No reliability report", {}, reliability_policy=policy
    )
    store.finish_experiment(experiment_id, "completed", {})
    store.freeze_study(experiment_id, _definition())

    with pytest.raises(ValidationRequiredError, match="reliability report"):
        store.authorize_final_holdout(experiment_id, _authorization())


def test_running_experiment_cannot_authorize_final_holdout(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    policy = ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "Still running", {}, reliability_policy=policy
    )
    store.record_study_reliability(
        experiment_id,
        policy,
        _report(policy, ReliabilityVerdict.RELIABLE),
    )
    store.freeze_study(experiment_id, _definition())

    with pytest.raises(ValidationRequiredError, match="completed"):
        store.authorize_final_holdout(experiment_id, _authorization())


def test_policy_mismatch_cannot_authorize_final_holdout(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = _completed_experiment(store)
    store._conn.execute("DROP TRIGGER immutable_reliability_report")
    store._conn.execute(
        """INSERT INTO reliability_policies
           (policy_hash, policy_version, payload_json, recorded_at)
           VALUES (?, 2, '{}', ?)""",
        ("e" * 64, datetime.now(timezone.utc).isoformat()),
    )
    store._conn.execute(
        """UPDATE study_reliability_reports
           SET policy_hash = ? WHERE experiment_id = ?""",
        ("e" * 64, experiment_id),
    )
    store._conn.commit()

    with pytest.raises(ImmutableRecordError, match="does not match"):
        store.authorize_final_holdout(experiment_id, _authorization())


@pytest.mark.parametrize(
    ("engine_integrity_complete", "procedure_state", "message"),
    [
        (False, "completed", "engine-integrity"),
        (True, "interrupted", "reliability procedure"),
    ],
)
def test_incomplete_reliability_prerequisites_block_authorization(
    tmp_path, engine_integrity_complete, procedure_state, message
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    policy = ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "Incomplete reliability prerequisites", {}, reliability_policy=policy
    )
    store.finish_experiment(experiment_id, "completed", {})
    report = _report(policy, ReliabilityVerdict.RELIABLE).model_copy(
        update={
            "engine_integrity_complete": engine_integrity_complete,
            "procedure_state": procedure_state,
        }
    )
    store.record_study_reliability(experiment_id, policy, report)
    store.freeze_study(experiment_id, _definition())

    with pytest.raises(ValidationRequiredError, match=message):
        store.authorize_final_holdout(experiment_id, _authorization())


def test_sql_trigger_refuses_direct_authorization_for_rejected_study(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = _completed_experiment(
        store, verdict=ReliabilityVerdict.REJECTED
    )

    with pytest.raises(
        Exception,
        match="completed, frozen study with a matching reliable verdict",
    ):
        store._conn.execute(
            """INSERT INTO final_holdout_authorizations
               (experiment_id, authorization_sha256, requested_by, reason,
                state, authorized_at)
               VALUES (?, ?, 'Bloom', ?, 'authorized', ?)""",
            (
                experiment_id,
                "f" * 64,
                "Direct SQL must not bypass reliability",
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def test_claim_revalidates_the_authorized_eligibility_basis(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = _completed_experiment(store)
    authorization = _authorization()
    store.authorize_final_holdout(experiment_id, authorization)

    store._conn.execute("DROP TRIGGER immutable_reliability_report")
    payload = store._conn.execute(
        """SELECT payload_json FROM study_reliability_reports
           WHERE experiment_id = ?""",
        (experiment_id,),
    ).fetchone()["payload_json"]
    changed_report = StudyReliabilityReport.model_validate_json(payload).model_copy(
        update={"verdict_reason": "Reliability evidence changed after authorization"}
    )
    store._conn.execute(
        """UPDATE study_reliability_reports
           SET payload_json = ? WHERE experiment_id = ?""",
        (changed_report.model_dump_json(), experiment_id),
    )
    store._conn.commit()

    with pytest.raises(ImmutableRecordError, match="basis is missing or stale"):
        store.claim_final_holdout(experiment_id, authorization.token)


@pytest.mark.parametrize(
    "basis_column",
    [
        "reliability_policy_hash",
        "frozen_definition_hash",
        "reliability_report_hash",
    ],
)
def test_authorized_to_claimed_cannot_mutate_eligibility_basis(
    tmp_path, basis_column
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = _completed_experiment(store)
    authorization = _authorization()
    store.authorize_final_holdout(experiment_id, authorization)

    with pytest.raises(Exception, match="illegal final holdout"):
        store._conn.execute(
            f"""UPDATE final_holdout_authorizations
                SET state = 'claimed', claimed_at = ?, claim_id = ?,
                    {basis_column} = ?
                WHERE experiment_id = ?""",
            (
                datetime.now(timezone.utc).isoformat(),
                f"claim-{basis_column}",
                f"forged-{basis_column}",
                experiment_id,
            ),
        )


@pytest.mark.parametrize("target_state", ["completed", "interrupted"])
@pytest.mark.parametrize(
    "basis_column",
    [
        "reliability_policy_hash",
        "frozen_definition_hash",
        "reliability_report_hash",
    ],
)
def test_claimed_to_terminal_cannot_mutate_eligibility_basis(
    tmp_path, basis_column, target_state
):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    experiment_id = _completed_experiment(store)
    authorization = _authorization()
    store.authorize_final_holdout(experiment_id, authorization)
    store.claim_final_holdout(experiment_id, authorization.token)

    with pytest.raises(Exception, match="illegal final holdout"):
        store._conn.execute(
            f"""UPDATE final_holdout_authorizations
                SET state = ?, completed_at = ?, {basis_column} = ?
                WHERE experiment_id = ?""",
            (
                target_state,
                datetime.now(timezone.utc).isoformat(),
                f"forged-{basis_column}",
                experiment_id,
            ),
        )


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
