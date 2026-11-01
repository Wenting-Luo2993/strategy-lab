"""Synthetic engine results through the real driver, recorder and SQLite store."""

from datetime import date, datetime, time
import json
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from vibe.backtester.analysis.performance import PerformanceAnalyzer
from vibe.backtester.core.integrity import (
    EngineIntegrityCategory,
    EngineIntegrityReport,
    EngineIntegrityStatus,
)
from vibe.common.models.trade import Trade
from vibe.research_pipeline.contracts import RetentionProfile, SegmentRole
from vibe.research_pipeline.experiments import ExperimentRecorder, _evaluation
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.optimization import Optimizer, ParameterAxis, Selector, generate_grid
from vibe.research_pipeline.recording import BacktestRunSink
from vibe.research_pipeline.recording_executor import RecordingSegmentExecutor
from vibe.research_pipeline.splits.calendar import SessionCalendar
from vibe.research_pipeline.splits.planner import SplitSpec, TemporalSplitPlanner
from vibe.research_pipeline.storage import schema
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.store import ImmutableRecordError
from vibe.research_pipeline.validation import ValidationReport
from vibe.research_pipeline.walk_forward import NestedWalkForwardDriver


def build_experiment_fixture(db_path, *, record_test_only=False, fail_role=None):
    """Produce a clearly labelled, separate demo database without market data."""
    store = SqliteResearchStore(db_path)
    plan = TemporalSplitPlanner().plan(SplitSpec(
        dev_start=date(2020, 1, 2), dev_end=date(2020, 3, 31),
        final_oos_start=date(2020, 4, 1), final_oos_end=date(2020, 4, 30),
        train_sessions=10, validation_sessions=5, test_sessions=5, step_sessions=20,
        purge_padding_sessions=0, embargo_padding_sessions=0,
    ))
    candidates = generate_grid((ParameterAxis("orb_minutes", (5, 15, 30)),))
    recorder = ExperimentRecorder(
        store, title="DEMO - ORB duration 5 / 15 / 30 (synthetic data)",
        plan=plan, candidates=candidates,
        axes=[{"name": "orb_minutes", "path": "strategy.orb_duration_minutes", "values": [5, 15, 30]}],
        symbol="SYNTHETIC", strategy_id="viewer_demo", methodology_version="synthetic_demo/v1",
        record_test_only=record_test_only,
    )

    class Engine:
        def effective_ruleset(self, candidate):
            class Ruleset(BaseModel):
                orb_minutes: int
            return Ruleset(**candidate.parameter_map)

        def __call__(self, candidate, segment):
            if segment.role is fail_role:
                raise RuntimeError("Synthetic evaluation failure")
            minutes = candidate.parameter_map["orb_minutes"]
            score = minutes / 30 if segment.role is SegmentRole.TRAIN else 1 - abs(minutes - 15) / 30
            tz = ZoneInfo("America/New_York")
            sessions = SessionCalendar("XNYS").sessions_between(segment.start_session, segment.end_session)
            first = datetime.combine(segment.start_session, time(10), tzinfo=tz)
            last = datetime.combine(segment.end_session, time(15), tzinfo=tz)
            trades = [
                Trade(symbol="SYNTHETIC", side="buy", quantity=1,
                      entry_price=1000, exit_price=1000 + pnl,
                      entry_time=stamp, exit_time=stamp, initial_risk=100,
                      commission=1, exit_reason="TARGET" if pnl > 0 else "STOP")
                for stamp, pnl in [(first, (score * 2 + 1) * 100 + 1), (last, -99)]
            ]
            curve = [(first, 100_000.0)] + [
                (datetime.combine(day, time(16), tzinfo=tz),
                 100_000 + (index + 1) / len(sessions) * score * 200
                 + ([80, -120, 100, -80, 0][index % 5] if index < len(sessions) - 1 else 0))
                for index, day in enumerate(sessions)
            ]
            result = PerformanceAnalyzer.analyze(
                trades, curve, 100_000, "SYNTHETIC", first, last,
                "viewer_demo", "1",
                execution_diagnostics={"min_cash": 99_000, "max_gross_exposure_ratio": 0.01},
            )
            result.engine_integrity_report = EngineIntegrityReport(
                status=EngineIntegrityStatus.PASS,
                engine_version=1,
                metric_calculation_version=2,
                checked_categories=tuple(EngineIntegrityCategory),
                findings=(),
            )
            return SimpleNamespace(segment=segment, result=result)

    sink = BacktestRunSink(
        store, strategy_id="viewer_demo", symbol="SYNTHETIC",
        data_snapshot_id="synthetic-viewer-fixture", execution_model_version=6,
        methodology_version="synthetic_demo/v1", repo_root=".", starting_equity=100_000,
        retention=RetentionProfile.FULL,
    )
    executor = RecordingSegmentExecutor(
        Engine(), sink,
        record_roles=frozenset({SegmentRole.TEST}) if record_test_only else None,
        on_record=recorder.record_run,
    )

    def validate(candidate, result, scope):
        return ValidationReport(
            profile_name="SYNTHETIC DEMO - not real validation", scope=scope,
            target_state=RunState.COMPLETED, checked_categories=(),
            registry_hash_at_execution="a" * 64, current_registry_hash="a" * 64,
            leakage_passed=True,
        )

    score = lambda result: result.overall.expectancy_r
    driver = NestedWalkForwardDriver(
        Optimizer(executor, validate, score), Selector(executor, validate, score),
        executor, validate,
    )
    result = driver.run(plan, candidates)
    recorder.finish(result, recording_errors=sink.failures)
    store.close()
    return recorder.experiment_id, result


def test_full_study_retains_candidates_selection_and_explicit_run_links(tmp_path):
    path = tmp_path / "demo.sqlite3"
    experiment_id, result = build_experiment_fixture(path)
    assert result.state is RunState.COMPLETED
    with sqlite3.connect(path) as conn:
        definition, payload = conn.execute(
            "SELECT definition_json, result_json FROM research_experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        definition, payload = json.loads(definition), json.loads(payload)
        assert definition["axes"][0]["values"] == [5, 15, 30]
        assert payload["recording_errors"] == []
        assert payload["n_folds_completed"] == len(definition["plan"]["folds"])
        assert conn.execute("SELECT COUNT(*) FROM research_experiment_runs").fetchone()[0] == len(result.folds) * 7
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for fold in payload["folds"]:
            assert len(fold["train"]) == len(fold["validation"]) == 3
            assert isinstance(fold["train"][0]["expectancy_r"], float)
            assert isinstance(fold["selection"]["winner_score"], float)
            assert isinstance(fold["test"]["sharpe_ratio"], float)
            assert max(fold["train"], key=lambda item: item["score"])["parameters"] == {"orb_minutes": 30}
            assert fold["selection"]["parameters"] == {"orb_minutes": 15}
            assert fold["test"]["parameters"] == {"orb_minutes": 15}
        assert conn.execute(
            """SELECT COUNT(*) FROM research_experiment_runs e JOIN runs r ON r.run_id=e.run_id
               WHERE e.segment_role != r.segment_role"""
        ).fetchone()[0] == 0


def test_test_only_retains_comparisons_without_inventing_train_run_links(tmp_path):
    path = tmp_path / "demo.sqlite3"
    _, result = build_experiment_fixture(path, record_test_only=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT DISTINCT segment_role FROM research_experiment_runs").fetchall() == [("test",)]
        payload = json.loads(conn.execute("SELECT result_json FROM research_experiments").fetchone()[0])
        assert len(payload["folds"][0]["train"]) == 3
        assert conn.execute("SELECT COUNT(*) FROM research_experiment_runs").fetchone()[0] == len(result.folds)


def test_failed_fold_is_preserved_with_no_fabricated_selection_or_oos(tmp_path):
    path = tmp_path / "demo.sqlite3"
    _, result = build_experiment_fixture(path, fail_role=SegmentRole.VALIDATION)
    assert result.state is RunState.INCONCLUSIVE
    with sqlite3.connect(path) as conn:
        payload = json.loads(conn.execute("SELECT result_json FROM research_experiments").fetchone()[0])
        assert payload["stitched_oos"] is None
        assert len(payload["folds"][0]["validation_failures"]) == 3
        assert payload["folds"][0]["selection"] is None
        assert payload["folds"][0]["test"] is None


def test_repeated_experiments_share_runs_not_experiment_identity(tmp_path):
    path = tmp_path / "demo.sqlite3"
    first_id, _ = build_experiment_fixture(path)
    second_id, _ = build_experiment_fixture(path)
    assert first_id != second_id
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] * 2 == conn.execute(
            "SELECT COUNT(*) FROM research_experiment_runs"
        ).fetchone()[0]


def test_finished_experiments_cannot_be_rewritten_or_relinked(tmp_path):
    path = tmp_path / "demo.sqlite3"
    experiment_id, _ = build_experiment_fixture(path)
    store = SqliteResearchStore(path)
    try:
        with pytest.raises(ImmutableRecordError):
            store.finish_experiment(experiment_id, "completed", {})
        with sqlite3.connect(path) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute("UPDATE research_experiments SET title='changed'")
            run_id = conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="membership"):
            store.link_experiment_run(experiment_id, 100, "wrong", SegmentRole.TRAIN, run_id)
    finally:
        store.close()


def test_finished_calibration_experiment_can_be_archived_without_deleting_evidence(
    tmp_path,
):
    path = tmp_path / "demo.sqlite3"
    experiment_id, _ = build_experiment_fixture(path)
    store = SqliteResearchStore(path)
    try:
        before = store._conn.execute(
            "SELECT COUNT(*) FROM research_experiment_runs WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()[0]
        store.archive_experiment(
            experiment_id,
            archived_by="Bloom",
            reason="Superseded calibration study",
        )
        archive = store._conn.execute(
            """SELECT archived_by, reason FROM archived_research_experiments
               WHERE experiment_id = ?""",
            (experiment_id,),
        ).fetchone()
        assert tuple(archive) == ("Bloom", "Superseded calibration study")
        assert store._conn.execute(
            "SELECT COUNT(*) FROM research_experiment_runs WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()[0] == before
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._conn.execute(
                """UPDATE archived_research_experiments SET reason = 'changed'
                   WHERE experiment_id = ?""",
                (experiment_id,),
            )
    finally:
        store.close()


def test_additive_migration_keeps_v10_runs_unassigned(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        with monkeypatch.context() as patch:
            patch.setattr(schema, "_MIGRATIONS", schema._MIGRATIONS[:10])
            patch.setattr(schema, "SCHEMA_VERSION", 10)
            schema.migrate(conn)
        assert schema.migrate(conn) == 16
        assert conn.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0] == 0
        assert schema.migrate(conn) == 16


def test_nonfinite_metrics_are_retained_as_explicit_invalid_evidence():
    evaluation = SimpleNamespace(
        candidate=SimpleNamespace(candidate_id="c", parameter_map={"p": 1}),
        score=float("nan"),
        validation_report=SimpleNamespace(target_state=RunState.VALIDATION_FAILED, findings=[]),
        segment_result=SimpleNamespace(result=SimpleNamespace(
            overall=SimpleNamespace(expectancy_r=float("nan"), total_pnl=100, n_trades=2),
            equity=SimpleNamespace(sharpe_ratio=float("inf")),
        )),
    )
    payload = _evaluation(evaluation)
    assert payload["score"] is None
    assert payload["expectancy_r"] is None
    assert payload["sharpe_ratio"] is None
    assert payload["non_finite_metrics"] == ["expectancy_r", "sharpe_ratio"]
    assert payload["state"] == "validation_failed"
    json.dumps(payload, allow_nan=False)
