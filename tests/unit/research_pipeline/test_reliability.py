from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from vibe.backtester.core.integrity import (
    EngineIntegrityCategory,
    EngineIntegrityReport,
    EngineIntegrityStatus,
)
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.reliability import (
    ReliabilityPolicy,
    ReliabilityVerdict,
    RobustnessEvidence,
    evaluate_study_reliability,
)
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore


def _integrity():
    return EngineIntegrityReport(
        status=EngineIntegrityStatus.PASS,
        engine_version=1,
        metric_calculation_version=2,
        checked_categories=tuple(EngineIntegrityCategory),
        findings=(),
    )


def _study(
    expectancy: float,
    trades: int = 200,
    folds=(0.1, 0.1, 0.1),
    r_multiples=None,
):
    samples = tuple(r_multiples or [expectancy] * trades)
    per_fold = len(samples) // len(folds)
    fold_rows = []
    for index, value in enumerate(folds):
        start = index * per_fold
        end = len(samples) if index == len(folds) - 1 else start + per_fold
        result = SimpleNamespace(
            overall=SimpleNamespace(
                expectancy_r=value,
                r_multiples=samples[start:end],
            ),
            equity=SimpleNamespace(n_sessions=50),
            engine_integrity_report=_integrity(),
        )
        candidate = SimpleNamespace(coordinates=(index % 2,))
        selection = SimpleNamespace(
            selected=SimpleNamespace(score=value + 0.05, candidate=candidate),
            neighborhood_candidate_ids=("a", "b"),
        )
        fold_rows.append(SimpleNamespace(
            test_result=SimpleNamespace(result=result),
            selection=selection,
        ))
    return SimpleNamespace(
        state=RunState.COMPLETED,
        n_folds_completed=len(fold_rows),
        n_folds_expected=len(fold_rows),
        folds=tuple(fold_rows),
        stitched_oos=SimpleNamespace(
            n_trades=trades,
            expectancy_r=expectancy,
            first_session=date(2025, 1, 1),
            last_session=date(2025, 8, 1),
        ),
    )


def _robust():
    return RobustnessEvidence(
        cost_stress_expectancy_r=0.02,
        neighbor_expectancies_r=(0.01, 0.03),
        sign_change_detected=False,
        cliff_detected=False,
        regimes_predeclared=True,
        regime_count=2,
        max_single_regime_return_concentration=0.6,
        subsample_seed=7,
        subsample_identity="sample",
        bootstrap_expectancies_r=(0.01, 0.02),
    )


def test_negative_orb_reference_is_rejected_not_reliable():
    report = evaluate_study_reliability(
        _study(-0.0245, trades=80),
        ReliabilityPolicy(version=1),
        robustness=_robust(),
        symbols=("QQQ",),
    )
    assert report.verdict is ReliabilityVerdict.REJECTED


def test_v2_confidence_interval_makes_imprecise_negative_result_inconclusive():
    values = [-1.0] * 40 + [0.951] * 40
    report = evaluate_study_reliability(
        _study(
            sum(values) / len(values),
            trades=len(values),
            folds=(0.01, 0.01, 0.01),
            r_multiples=values,
        ),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ",),
    )

    confidence = next(
        item for item in report.criteria
        if item.criterion_id == "REL-OOS-CONFIDENCE"
    )
    assert confidence.outcome.value == "inconclusive"
    assert report.statistical_confidence["t_statistic"] == pytest.approx(-0.223, abs=0.01)
    assert report.statistical_confidence[
        "evidence_confidence_above_threshold"
    ] == pytest.approx(0.412, abs=0.01)
    assert "confidence interval" in report.verdict_reason


def test_v2_confidence_interval_passes_or_rejects_only_when_wholly_one_sided():
    passing = evaluate_study_reliability(
        _study(0.2, r_multiples=[0.1, 0.2, 0.3] * 70),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ",),
    )
    rejected = evaluate_study_reliability(
        _study(-0.2, r_multiples=[-0.1, -0.2, -0.3] * 70),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ",),
    )

    assert next(
        item.outcome.value for item in passing.criteria
        if item.criterion_id == "REL-OOS-CONFIDENCE"
    ) == "pass"
    assert next(
        item.outcome.value for item in rejected.criteria
        if item.criterion_id == "REL-OOS-CONFIDENCE"
    ) == "fail"


def test_too_little_otherwise_positive_evidence_is_inconclusive():
    report = evaluate_study_reliability(
        _study(0.1, trades=20),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ",),
    )
    assert report.verdict is ReliabilityVerdict.INCONCLUSIVE


def test_suspicious_non_dispositive_evidence_requires_review():
    report = evaluate_study_reliability(
        _study(0.1),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ",),
        plausibility_review_required=True,
    )
    assert report.verdict is ReliabilityVerdict.REVIEW_REQUIRED


def test_all_registered_criteria_produce_reliable():
    report = evaluate_study_reliability(
        _study(0.1),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ",),
    )
    assert report.verdict is ReliabilityVerdict.RELIABLE


def test_multi_symbol_claim_without_shared_capital_is_inconclusive():
    report = evaluate_study_reliability(
        _study(0.1),
        ReliabilityPolicy(),
        robustness=_robust(),
        symbols=("QQQ", "MSFT"),
        shared_capital_portfolio=False,
    )
    assert report.verdict is ReliabilityVerdict.INCONCLUSIVE


def test_policy_is_immutable_and_has_stable_identity():
    policy = ReliabilityPolicy(min_stitched_oos_trades=125)

    assert policy.policy_hash == ReliabilityPolicy(
        min_stitched_oos_trades=125
    ).policy_hash
    try:
        policy.min_stitched_oos_trades = 1
    except Exception as exc:
        assert "frozen" in str(exc).lower()
    else:  # pragma: no cover - pydantic must enforce the contract
        raise AssertionError("ReliabilityPolicy must be immutable")


def test_policy_v1_hash_excludes_v2_confidence_fields():
    legacy = ReliabilityPolicy(version=1)
    payload = legacy.model_dump(mode="python")
    payload.pop("expectancy_confidence_level")
    payload.pop("use_expectancy_confidence_interval")

    from vibe.research_pipeline.hashing import hash_object

    assert legacy.policy_hash == hash_object(payload)


@pytest.mark.parametrize(
    ("flag", "criterion_id", "robustness", "symbols"),
    [
        (
            "require_cost_stress_survival",
            "REL-ROBUST-COST",
            RobustnessEvidence(),
            ("QQQ",),
        ),
        (
            "require_parameter_neighborhood",
            "REL-ROBUST-NEIGHBORHOOD",
            RobustnessEvidence(),
            ("QQQ",),
        ),
        (
            "require_regime_evidence",
            "REL-ROBUST-REGIME",
            RobustnessEvidence(),
            ("QQQ",),
        ),
        (
            "require_seeded_subsampling",
            "REL-ROBUST-SUBSAMPLE",
            RobustnessEvidence(),
            ("QQQ",),
        ),
        (
            "require_shared_capital_for_multi_symbol",
            "REL-PORTFOLIO-001",
            _robust(),
            ("QQQ", "MSFT"),
        ),
    ],
)
def test_disabled_requirements_are_explicitly_advisory(
    flag, criterion_id, robustness, symbols
):
    policy = ReliabilityPolicy(**{flag: False})
    report = evaluate_study_reliability(
        _study(0.1),
        policy,
        robustness=robustness,
        symbols=symbols,
        shared_capital_portfolio=False,
    )
    criterion = next(item for item in report.criteria if item.criterion_id == criterion_id)
    assert criterion.outcome.value == "advisory"


def test_exact_robustness_evidence_and_provenance_are_persisted(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    policy = ReliabilityPolicy()
    experiment_id = store.start_experiment(
        "Robustness provenance", {}, reliability_policy=policy
    )
    robustness = _robust()
    report = evaluate_study_reliability(
        _study(0.1),
        policy,
        robustness=robustness,
        symbols=("QQQ",),
    )

    store.record_study_reliability(
        experiment_id, policy, report, robustness=robustness
    )

    row = store._conn.execute(
        """SELECT provenance_hash, payload_json FROM robustness_evidence
           WHERE experiment_id = ?""",
        (experiment_id,),
    ).fetchone()
    assert row["provenance_hash"] == robustness.provenance_hash
    assert RobustnessEvidence.model_validate_json(row["payload_json"]) == robustness

    for table, column, value in (
        ("robustness_evidence", "experiment_id", experiment_id),
        ("study_reliability_reports", "experiment_id", experiment_id),
        ("reliability_policies", "policy_hash", policy.policy_hash),
    ):
        with pytest.raises(Exception, match="immutable"):
            store._conn.execute(
                f"DELETE FROM {table} WHERE {column} = ?", (value,)
            )
        store._conn.rollback()
