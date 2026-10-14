"""What the study-wide run sink must guarantee.

A sweep, a walk-forward, and a robustness pass all feed the same store. These
tests pin the three properties that make the resulting population trustworthy:
every point is distinguishable, an in-sample fold can never be mistaken for an
out-of-sample one, and bookkeeping trouble never destroys a finished study.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pandas as pd
import pytest
from pydantic import BaseModel

from vibe.research_pipeline.contracts import RetentionProfile, SegmentRole
from vibe.research_pipeline.lifecycle import RunState
from vibe.research_pipeline.recording import BacktestRunSink, hash_effective_ruleset
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore

STARTING_EQUITY = 100_000.0


class _Ruleset(BaseModel):
    """Stands in for a strategy ruleset, including the ``time`` field that
    makes naive canonical encoding fail."""

    orb_start_time: time = time(9, 30)
    stop_atr_multiple: float = 1.5


class _Trade(SimpleNamespace):
    pass


def _trade(pnl: float = 30.0) -> _Trade:
    return _Trade(
        symbol="QQQ",
        side="long",
        entry_time=datetime(2023, 3, 1, 14, 35, tzinfo=timezone.utc),
        exit_time=datetime(2023, 3, 1, 15, 5, tzinfo=timezone.utc),
        entry_price=300.0,
        exit_price=303.0,
        quantity=10.0,
        pnl=pnl,
        exit_reason="target",
    )


def _result(
    *,
    first: date = date(2023, 3, 1),
    last: date = date(2023, 3, 1),
    n_sessions: int = 1,
) -> SimpleNamespace:
    index = pd.to_datetime(
        ["2023-03-01 14:35", "2023-03-01 14:40", "2023-03-01 15:05"], utc=True
    )
    curve = pd.Series(
        [STARTING_EQUITY, STARTING_EQUITY + 50.0, STARTING_EQUITY + 100.0],
        index=index,
    )
    return SimpleNamespace(
        trades=[_trade(), _trade(70.0)],
        equity=SimpleNamespace(equity_curve=curve, n_sessions=n_sessions),
        execution_diagnostics={"max_gross_exposure_ratio": 0.8, "min_cash": 42_000.0},
        overall=SimpleNamespace(
            gross_pnl=120.0,
            total_costs=20.0,
            total_pnl=100.0,
            first_date=first,
            last_date=last,
            calculation_version=1,
        ),
    )


@pytest.fixture
def stub_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """The metric set has its own tests; isolate the sink from it."""
    monkeypatch.setattr(
        "vibe.research_pipeline.validation.metric_values",
        lambda result: {"net_pnl": float(result.overall.total_pnl)},
    )


def _sink(tmp_path: Path, **overrides: Any) -> BacktestRunSink:
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    kwargs: dict[str, Any] = dict(
        strategy_id="orb",
        symbol="QQQ",
        data_snapshot_id="sha256:deadbeef",
        execution_model_version=1,
        methodology_version="test/v1",
        repo_root=Path(__file__).resolve().parents[3],
        starting_equity=STARTING_EQUITY,
        retention=RetentionProfile.FULL,
        owner="test",
    )
    kwargs.update(overrides)
    return BacktestRunSink(store, **kwargs)


# -- every swept point must be its own run -------------------------------


def test_points_that_differ_only_in_parameters_are_separate_runs(tmp_path, stub_metrics):
    sink = _sink(tmp_path)

    for multiple in (1.0, 1.5, 2.0):
        outcome = sink.record(
            _result(),
            ruleset=_Ruleset(stop_atr_multiple=multiple),
            parameters={"stop_atr_multiple": multiple},
        )
        assert outcome is not None and outcome.recorded

    assert len(set(sink.recorded_run_ids)) == 3
    assert sink.duplicate_run_ids == []
    assert sink.failures == []


def test_an_identical_point_is_reported_not_recorded_twice(tmp_path, stub_metrics):
    """A ``one_at_a_time`` sweep revisits its base combination, and integer
    slippage grids collapse. Repetition is normal use, so it must not abort
    the study or silently inflate the population."""
    sink = _sink(tmp_path)
    args = dict(ruleset=_Ruleset(), parameters={"stop_atr_multiple": 1.5})

    first = sink.record(_result(), **args)
    second = sink.record(_result(), **args)

    assert first is not None and first.recorded
    assert second is not None and not second.recorded
    assert second.run_id == first.run_id
    assert sink.recorded_run_ids == [first.run_id]
    assert sink.duplicate_run_ids == [first.run_id]
    assert sink.failures == []


# -- in-sample must never read as out-of-sample --------------------------


def test_a_train_fold_is_a_different_run_from_an_identical_test_fold(
    tmp_path, stub_metrics
):
    sink = _sink(tmp_path)
    args = dict(ruleset=_Ruleset(), parameters={"stop_atr_multiple": 1.5})

    train = sink.record(_result(), segment_role=SegmentRole.TRAIN, **args)
    test = sink.record(_result(), segment_role=SegmentRole.TEST, **args)

    assert train is not None and train.recorded
    assert test is not None and test.recorded, (
        "A train fold and a test fold over the same bars must not collide; "
        "if they do, an in-sample number is readable as out-of-sample evidence."
    )
    assert train.run_id != test.run_id


def test_the_role_is_structural_not_just_a_label(tmp_path, stub_metrics):
    """The role lives inside the fingerprint, so it is impossible to relabel a
    stored train fold as a test fold after the fact."""
    from vibe.research_pipeline.recording import build_run_fingerprint

    common = dict(
        strategy_id="orb",
        ruleset_sha256=hash_effective_ruleset(_Ruleset()),
        parameters={},
        symbol="QQQ",
        first_session=date(2023, 3, 1),
        last_session=date(2023, 3, 1),
        session_count=1,
        data_snapshot_id="sha256:deadbeef",
        execution_model_version=1,
        metric_calculation_version=1,
        repo_root=".",
        code_commit="a1b2c3d4e5f6",
        code_dirty=False,
    )

    train = build_run_fingerprint(segment_role=SegmentRole.TRAIN, **common)
    test = build_run_fingerprint(segment_role=SegmentRole.TEST, **common)

    assert train.split_manifest_hash != test.split_manifest_hash, (
        "The role must be folded into the split manifest hash; otherwise a "
        "stored train fold is indistinguishable from a test fold."
    )
    assert train.fingerprint != test.fingerprint


# -- bookkeeping must never destroy a finished study ---------------------


def test_a_failure_is_collected_rather_than_raised(tmp_path, stub_metrics):
    """Hours of compute must not be lost because the store could not take a
    row. The failure is still surfaced -- it is reported, not swallowed."""
    sink = _sink(tmp_path)
    broken = _result()
    broken.overall.first_date = None  # unreadable session boundary

    outcome = sink.record(broken, ruleset=_Ruleset(), parameters={})

    assert outcome is None
    assert sink.recorded_run_ids == []
    assert len(sink.failures) == 1
    assert "failed" in sink.summary()


def test_the_effective_ruleset_hash_tracks_the_configuration(tmp_path):
    """The hash must change when the configuration does -- otherwise every
    sweep point would claim to have used the same ruleset."""
    assert hash_effective_ruleset(_Ruleset()) == hash_effective_ruleset(_Ruleset())
    assert hash_effective_ruleset(_Ruleset()) != hash_effective_ruleset(
        _Ruleset(stop_atr_multiple=2.0)
    )
