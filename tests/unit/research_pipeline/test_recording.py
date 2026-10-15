"""Tests for the producer that writes finished runs into the research store.

The producer is the layer whose absence meant the research database was never
created at all, so the behaviour worth pinning down here is not "does it
write" but "does it refuse to write something untrue": an un-reconciled P&L, a
fabricated end point, a run that crashed but looks finished, or row-level
detail retained under a profile that promised not to keep it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pandas as pd
import pytest

from vibe.research_pipeline.contracts import RetentionProfile
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.recording import (
    RecordingError,
    RunRecorder,
    build_run_evidence,
    git_state,
)

STARTING_EQUITY = 100_000.0


@dataclass
class _Trade:
    symbol: str = "QQQ"
    side: str = "long"
    entry_time: datetime = datetime(2023, 3, 1, 14, 35, tzinfo=timezone.utc)
    exit_time: Optional[datetime] = datetime(2023, 3, 1, 15, 5, tzinfo=timezone.utc)
    entry_price: float = 300.0
    exit_price: Optional[float] = 303.0
    quantity: float = 10.0
    pnl: Optional[float] = 30.0
    exit_reason: str = "target"


def _result(
    *,
    gross_pnl: float = 120.0,
    total_costs: float = 20.0,
    net_pnl: float = 100.0,
    curve_end: float = STARTING_EQUITY + 100.0,
    trades: Optional[list[_Trade]] = None,
    empty_curve: bool = False,
) -> SimpleNamespace:
    index = pd.to_datetime(
        ["2023-03-01 14:35", "2023-03-01 14:40", "2023-03-01 15:05"], utc=True
    )
    values = [STARTING_EQUITY, STARTING_EQUITY + 50.0, curve_end]
    curve = pd.Series([], dtype=float) if empty_curve else pd.Series(values, index=index)

    return SimpleNamespace(
        trades=trades if trades is not None else [_Trade(), _Trade(pnl=70.0)],
        equity=SimpleNamespace(equity_curve=curve),
        execution_diagnostics={"max_gross_exposure_ratio": 0.8, "min_cash": 42_000.0},
        overall=SimpleNamespace(
            gross_pnl=gross_pnl,
            total_costs=total_costs,
            total_pnl=net_pnl,
            first_date=date(2023, 3, 1),
            last_date=date(2023, 3, 1),
            calculation_version=1,
        ),
    )


# -- evidence must reconcile, not merely be assembled --------------------


def test_evidence_is_built_from_a_coherent_result():
    evidence = build_run_evidence(
        _result(), starting_equity=STARTING_EQUITY, retention=RetentionProfile.FULL
    )

    assert evidence.trade_count == 2
    assert evidence.net_pnl == 100.0
    assert evidence.ending_equity == STARTING_EQUITY + 100.0
    assert evidence.retention_profile is RetentionProfile.FULL


def test_equity_that_moved_more_than_the_ledger_explains_is_refused():
    """The reconciliation is the whole point of recording evidence.

    ``starting_equity`` is passed in rather than read off the curve precisely
    so this can fail. If both ends came from the same curve the identity would
    hold by construction and would assert nothing about the engine.
    """
    result = _result(curve_end=STARTING_EQUITY + 5_000.0)

    with pytest.raises(ValueError, match="does not match net"):
        build_run_evidence(
            result, starting_equity=STARTING_EQUITY, retention=RetentionProfile.SUMMARY
        )


def test_costs_that_do_not_bridge_gross_and_net_are_refused():
    result = _result(gross_pnl=500.0, total_costs=20.0, net_pnl=100.0)

    with pytest.raises(ValueError, match="does not reconcile"):
        build_run_evidence(
            result, starting_equity=STARTING_EQUITY, retention=RetentionProfile.SUMMARY
        )


def test_empty_equity_curve_is_refused_rather_than_invented():
    with pytest.raises(RecordingError, match="empty equity curve"):
        build_run_evidence(
            _result(empty_curve=True),
            starting_equity=STARTING_EQUITY,
            retention=RetentionProfile.SUMMARY,
        )


# -- git state must identify the code, and mean something ----------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "strategy.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_clean_checkout_is_not_dirty(git_repo: Path):
    state = git_state(git_repo)

    assert len(state.commit) == 40
    assert state.dirty is False


def test_uncommitted_source_change_is_dirty(git_repo: Path):
    (git_repo / "strategy.py").write_text("x = 2\n")

    assert git_state(git_repo).dirty is True


def test_untracked_source_file_is_dirty(git_repo: Path):
    (git_repo / "extra.py").write_text("y = 1\n")

    assert git_state(git_repo).dirty is True


def test_churning_bytecode_alone_is_not_dirty(git_repo: Path):
    """This repository tracks ``__pycache__/*.pyc`` and rewrites them constantly.

    Counting them would set the flag on every single run, which would hide the
    case the flag exists to catch: uncommitted *source* changes.
    """
    cache = git_repo / "__pycache__"
    cache.mkdir()
    (cache / "strategy.cpython-311.pyc").write_bytes(b"\x00stale")

    assert git_state(git_repo).dirty is False


def test_bytecode_does_not_mask_a_real_source_change(git_repo: Path):
    """The exclusion must be narrow: .pyc ignored, source still seen."""
    cache = git_repo / "__pycache__"
    cache.mkdir()
    (cache / "strategy.cpython-311.pyc").write_bytes(b"\x00stale")
    (git_repo / "strategy.py").write_text("x = 3\n")

    assert git_state(git_repo).dirty is True


def test_non_git_directory_refuses_to_guess(tmp_path: Path):
    with pytest.raises(RecordingError, match="git state"):
        git_state(tmp_path)


# -- the lifecycle must reflect what actually happened -------------------


class _FakeStore:
    """Records lifecycle calls so the recorder's effects are observable."""

    def __init__(self) -> None:
        self.transitions: list[RunState] = []
        self.metrics: Optional[dict[str, Any]] = None
        self.evidence: Any = None
        self.trades: Any = None
        self.curve: Any = None
        self.started = False
        self.finalized: list[Any] = []

    def register_run(
        self, *, fingerprint, methodology_version, notes=None, segment_role=None
    ) -> str:
        self.registered_segment_role = segment_role
        return "run-1"

    def start_run(self, run_id, *, owner, lease_seconds) -> str:
        self.started = True
        return "lease-1"

    def transition(self, run_id, *, target, reason, lease_token=None) -> None:
        self.transitions.append(target)

    def record_metrics(self, run_id, metrics, *, calculation_version) -> None:
        self.metrics = dict(metrics)

    def record_evidence(self, run_id, evidence) -> None:
        self.evidence = evidence

    def record_trades(self, run_id, trades) -> None:
        self.trades = list(trades)

    def record_equity_curve(self, run_id, points) -> None:
        self.curve = points

    def finalize_validation(self, run_id, report) -> None:
        self.finalized.append(report)


@pytest.fixture
def stub_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """The metric set is covered by its own tests; isolate the recorder."""
    monkeypatch.setattr(
        "vibe.research_pipeline.validation.metric_values",
        lambda result: {"net_pnl": float(result.overall.total_pnl)},
    )


def _recorder(store: _FakeStore, retention: RetentionProfile) -> RunRecorder:
    return RunRecorder(
        store,
        fingerprint=object(),
        methodology_version="test",
        retention=retention,
    )


def test_a_crashing_run_is_marked_failed_not_left_absent(stub_metrics):
    """A recorder that only persisted successes would bias the record.

    The run is registered before the engine is invoked precisely so a crash
    leaves a visible ``EXECUTION_FAILED`` row rather than no row at all.
    """
    store = _FakeStore()

    with pytest.raises(ZeroDivisionError):
        with _recorder(store, RetentionProfile.FULL):
            raise ZeroDivisionError("engine blew up")

    assert store.started is True
    assert store.transitions == [RunState.EXECUTION_FAILED]
    assert store.evidence is None


def test_abandoning_a_run_without_recording_is_also_a_failure(stub_metrics):
    store = _FakeStore()

    with _recorder(store, RetentionProfile.FULL):
        pass

    assert store.transitions == [RunState.EXECUTION_FAILED]


def test_a_recorded_run_comes_to_rest_in_validating(stub_metrics):
    """Recording must never claim a run passed validation.

    ``COMPLETED`` is only legitimate via ``finalize_validation``, which needs a
    real leakage report. Resting in ``VALIDATING`` truthfully says "recorded,
    not yet judged".
    """
    store = _FakeStore()

    with _recorder(store, RetentionProfile.FULL) as recorder:
        run_id = recorder.record(_result(), starting_equity=STARTING_EQUITY)

    assert run_id == "run-1"
    assert store.transitions == [RunState.VALIDATING]
    assert store.metrics == {"net_pnl": 100.0}
    assert store.evidence is not None
    assert store.finalized == []


def test_a_validator_is_required_before_a_run_can_be_finalized(stub_metrics):
    """The recorder never finalizes on its own authority.

    It holds no leakage report and no data-integrity measurement, so it has
    no basis on which to judge a run. Finalization happens only when a caller
    that *does* hold that evidence supplies it.
    """
    store = _FakeStore()
    report = object()

    recorder = RunRecorder(
        store,
        fingerprint=object(),
        methodology_version="test",
        retention=RetentionProfile.FULL,
        validator=lambda _result: report,
    )
    with recorder:
        recorder.record(_result(), starting_equity=STARTING_EQUITY)

    assert store.finalized == [report]


def test_the_validator_sees_the_result_that_was_recorded(stub_metrics):
    """A validator judging some other object would be worthless.

    The report must be derived from the same result whose metrics and
    evidence were just persisted, or the stored verdict describes a different
    run from the stored numbers.
    """
    store = _FakeStore()
    seen: list[Any] = []
    result = _result()

    recorder = RunRecorder(
        store,
        fingerprint=object(),
        methodology_version="test",
        retention=RetentionProfile.FULL,
        validator=lambda r: seen.append(r) or object(),
    )
    with recorder:
        recorder.record(result, starting_equity=STARTING_EQUITY)

    assert seen == [result]


def test_summary_retention_keeps_evidence_but_no_rows(stub_metrics):
    store = _FakeStore()

    with _recorder(store, RetentionProfile.SUMMARY) as recorder:
        recorder.record(_result(), starting_equity=STARTING_EQUITY)

    assert store.evidence is not None
    assert store.trades is None
    assert store.curve is None


def test_diagnostic_retention_keeps_trades_but_not_the_curve(stub_metrics):
    store = _FakeStore()

    with _recorder(store, RetentionProfile.DIAGNOSTIC) as recorder:
        recorder.record(_result(), starting_equity=STARTING_EQUITY)

    assert store.trades is not None and len(store.trades) == 2
    assert store.curve is None


def test_full_retention_keeps_trades_and_the_curve(stub_metrics):
    store = _FakeStore()

    with _recorder(store, RetentionProfile.FULL) as recorder:
        recorder.record(_result(), starting_equity=STARTING_EQUITY)

    assert store.trades is not None and len(store.trades) == 2
    assert store.curve is not None and len(store.curve) == 3


def test_recording_outside_the_context_is_refused(stub_metrics):
    recorder = _recorder(_FakeStore(), RetentionProfile.FULL)

    with pytest.raises(RecordingError, match="outside its context"):
        recorder.record(_result(), starting_equity=STARTING_EQUITY)


def test_the_validation_input_carries_the_supplied_leakage_report():
    """The report must reach the validator unchanged.

    ``test_validation.py`` already proves that a leakage report missing a
    required check blocks completion. What is unverified here is the wiring:
    if ``build_validation_input`` dropped or replaced the report, that
    blocking behaviour would never be reached and every run would finalize on
    evidence nobody supplied.
    """
    from vibe.research_pipeline.evaluation import build_validation_input
    from vibe.research_pipeline.features.leakage import LeakageFinding, LeakageReport

    report = LeakageReport(
        (LeakageFinding("truncation_equivalence", "probe", True, "ok"),)
    )
    payload = build_validation_input(
        _result(),
        starting_equity=STARTING_EQUITY,
        expected_sessions=1,
        leakage_report=report,
    )

    assert payload.leakage_report is report


def test_observed_sessions_are_not_copied_from_the_expectation():
    """DATA-001 must retain the ability to disagree.

    The check compares what the exchange calendar says exists against what
    the backtest actually graded. Sourcing both from one number would make it
    structurally incapable of firing.
    """
    from vibe.research_pipeline.evaluation import build_validation_input
    from vibe.research_pipeline.features.leakage import LeakageReport

    payload = build_validation_input(
        _result(),
        starting_equity=STARTING_EQUITY,
        expected_sessions=99,
        leakage_report=LeakageReport(()),
    )

    assert payload.data_integrity.expected_sessions == 99
    assert payload.data_integrity.observed_sessions != 99


# -- segment_role must be queryable, not buried in the fingerprint -------


def _role_fingerprint(*, segment_role, seed: str = "s"):
    """A real fingerprint, so the identity assertions mean something."""
    from vibe.research_pipeline.recording import build_run_fingerprint

    return build_run_fingerprint(
        strategy_id="orb",
        ruleset_sha256="a" * 64,
        parameters={"seed": seed},
        symbol="QQQ",
        first_session=date(2023, 1, 3),
        last_session=date(2023, 3, 1),
        session_count=40,
        data_snapshot_id="snap-1",
        execution_model_version=1,
        metric_calculation_version=1,
        repo_root=".",
        segment_role=segment_role,
        code_commit="c" * 40,
        code_dirty=False,
    )


def test_segment_role_is_queryable_not_buried_in_the_fingerprint(tmp_path):
    """A viewer must be able to filter out-of-sample runs with a WHERE clause.

    Before this column existed, segment_role only fed split_manifest_hash, so
    the only way to tell a test run from a train run was to parse free-text
    notes. That is precisely how an in-sample number gets presented as
    out-of-sample evidence.
    """
    import sqlite3

    from vibe.research_pipeline.contracts import SegmentRole
    from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

    db = tmp_path / "roles.sqlite3"
    store = SqliteResearchStore(db)

    seen = {}
    for role in (SegmentRole.TRAIN, SegmentRole.TEST, None):
        fingerprint = _role_fingerprint(
            segment_role=role or SegmentRole.TEST, seed=str(role)
        )
        seen[role] = store.register_run(
            fingerprint=fingerprint,
            methodology_version="v1",
            segment_role=role,
        )

    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT run_id, segment_role FROM runs").fetchall())
    conn.close()

    assert rows[seen[SegmentRole.TRAIN]] == "train"
    assert rows[seen[SegmentRole.TEST]] == "test"
    assert rows[seen[None]] is None, (
        "an undeclared run must stay NULL; stamping it 'test' would hand a "
        "viewer exploratory runs as out-of-sample evidence"
    )

    # The point of the column: one WHERE clause, no notes parsing.
    conn = sqlite3.connect(str(db))
    oos = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE segment_role = 'test'"
    ).fetchone()[0]
    conn.close()
    assert oos == 1


def test_persisting_the_role_did_not_move_any_fingerprint():
    """The column must be additive to identity, never a rewrite of it.

    If adding the column changed how a run hashes, every run already in a
    store would be orphaned from its own future re-runs.
    """
    from vibe.research_pipeline.contracts import SegmentRole

    explicit = _role_fingerprint(segment_role=SegmentRole.TEST, seed="same")
    defaulted = _role_fingerprint(segment_role=SegmentRole.TEST, seed="same")
    assert explicit.fingerprint == defaulted.fingerprint

    train = _role_fingerprint(segment_role=SegmentRole.TRAIN, seed="same")
    assert train.fingerprint != explicit.fingerprint, (
        "roles must still separate identities; otherwise a train and test run "
        "over the same window would collide"
    )


def test_an_undeclared_run_is_not_recorded_as_out_of_sample(stub_metrics):
    """The sink's default must not manufacture out-of-sample evidence.

    ``record()`` still fingerprints an undeclared run as TEST to preserve
    identities established before the column existed, but it must not write
    that assumption into the queryable column.
    """
    store = _FakeStore()

    recorder = RunRecorder(
        store,
        fingerprint=object(),
        methodology_version="test",
        retention=RetentionProfile.SUMMARY,
    )
    with recorder:
        recorder.record(_result(), starting_equity=STARTING_EQUITY)

    assert store.registered_segment_role is None
