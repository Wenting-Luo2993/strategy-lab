"""The recording decorator must label runs from the segment, not the caller."""

from __future__ import annotations

from datetime import date

import pytest
from types import SimpleNamespace

from vibe.research_pipeline.contracts import SegmentRole, SessionSegment
from vibe.research_pipeline.recording_executor import RecordingSegmentExecutor


class _Candidate:
    def __init__(self, candidate_id: str = "c1", **parameters: object) -> None:
        self.candidate_id = candidate_id
        self.parameter_map = parameters or {"orb_minutes": 15}


class _Segment:
    """A stand-in carrying the one attribute the decorator reads."""

    def __init__(self, role: SegmentRole) -> None:
        self.role = role
        self.start_session = date(2024, 1, 2)
        self.end_session = date(2024, 2, 1)


class _InnerResult:
    def __init__(self, marker: str) -> None:
        self.result = f"backtest-{marker}"


class _Inner:
    """Executor double that records what it was asked to do."""

    def __init__(self, explode: bool = False) -> None:
        self.calls: list[tuple[object, object]] = []
        self._explode = explode

    def __call__(self, candidate, segment):
        self.calls.append((candidate, segment))
        return _InnerResult(segment.role.value)

    def effective_ruleset(self, candidate):
        if self._explode:
            raise RuntimeError("ruleset unavailable")
        return {"effective": candidate.parameter_map}


class _Sink:
    def __init__(self, explode: bool = False) -> None:
        self.records: list[dict] = []
        self._explode = explode

    def record(self, result, **kwargs):
        if self._explode:
            raise RuntimeError("store is unreachable")
        self.records.append({"result": result, **kwargs})
        return None


def test_each_segment_is_recorded_under_the_role_it_actually_ran_on():
    """The role must be read off the segment, not asserted by the caller.

    This is the whole point of decorating the executor rather than recording
    at the call site: the driver evaluates train, validation and test windows
    through one executor, so a run cannot be filed under a role it was not
    run on.
    """
    inner, sink = _Inner(), _Sink()
    executor = RecordingSegmentExecutor(inner, sink)

    for role in (SegmentRole.TRAIN, SegmentRole.VALIDATION, SegmentRole.TEST):
        executor(_Candidate(), _Segment(role))

    assert [r["segment_role"] for r in sink.records] == [
        SegmentRole.TRAIN,
        SegmentRole.VALIDATION,
        SegmentRole.TEST,
    ]
    # And the recorded payload is the unwrapped BacktestResult, not the
    # SegmentResult wrapper the sink cannot grade.
    assert [r["result"] for r in sink.records] == [
        "backtest-train",
        "backtest-validation",
        "backtest-test",
    ]


def test_the_recorded_ruleset_is_the_one_that_executed():
    """Recording the base ruleset would file every swept point identically."""
    inner, sink = _Inner(), _Sink()
    executor = RecordingSegmentExecutor(inner, sink)

    executor(_Candidate("a", orb_minutes=5), _Segment(SegmentRole.TEST))
    executor(_Candidate("b", orb_minutes=30), _Segment(SegmentRole.TEST))

    rulesets = [r["ruleset"] for r in sink.records]
    assert rulesets[0] != rulesets[1], (
        "two grid points recorded the same effective ruleset, which would "
        "make them indistinguishable in the store"
    )
    assert rulesets[0] == {"effective": {"orb_minutes": 5}}
    assert [r["parameters"] for r in sink.records] == [
        {"orb_minutes": 5},
        {"orb_minutes": 30},
    ]


def test_restricting_roles_skips_without_recording_them():
    inner, sink = _Inner(), _Sink()
    executor = RecordingSegmentExecutor(
        inner, sink, record_roles=frozenset({SegmentRole.TEST})
    )

    executor(_Candidate(), _Segment(SegmentRole.TRAIN))
    executor(_Candidate(), _Segment(SegmentRole.VALIDATION))
    result = executor(_Candidate(), _Segment(SegmentRole.TEST))

    assert [r["segment_role"] for r in sink.records] == [SegmentRole.TEST]
    assert executor.skipped_roles == {"train": 1, "validation": 1}
    # Skipping a recording must not skip the evaluation.
    assert len(inner.calls) == 3
    assert result.result == "backtest-test"


def test_a_failing_store_cannot_change_the_walk_forward_result():
    """A store problem is an infrastructure fact, not a research finding."""
    inner, sink = _Inner(), _Sink(explode=True)
    executor = RecordingSegmentExecutor(inner, sink)

    result = executor(_Candidate(), _Segment(SegmentRole.TEST))

    assert result.result == "backtest-test"
    assert sink.records == []


def test_an_executor_that_cannot_pin_its_ruleset_is_refused_up_front():
    """Fail at construction, not after a multi-hour walk-forward has run."""

    class _Bare:
        def __call__(self, candidate, segment):
            return _InnerResult("x")

    with pytest.raises(TypeError, match="effective_ruleset"):
        RecordingSegmentExecutor(_Bare(), _Sink())


def test_the_note_names_the_role_and_window():
    inner, sink = _Inner(), _Sink()
    executor = RecordingSegmentExecutor(inner, sink)

    executor(_Candidate("c7"), _Segment(SegmentRole.TEST))

    note = sink.records[0]["notes"]
    assert "test" in note
    assert "2024-01-02" in note and "2024-02-01" in note
    assert "c7" in note


def test_a_real_session_segment_is_accepted():
    """Guard against the double drifting from the contract it stands in for."""
    inner, sink = _Inner(), _Sink()
    executor = RecordingSegmentExecutor(inner, sink)

    segment = SessionSegment(
        role=SegmentRole.TEST,
        start_session=date(2024, 1, 2),
        end_session=date(2024, 2, 1),
        session_count=22,
    )
    executor(_Candidate(), segment)

    assert sink.records[0]["segment_role"] is SegmentRole.TEST


def test_link_callback_receives_reused_run_identity_and_actual_segment():
    class ReusingSink(_Sink):
        def record(self, result, **kwargs):
            super().record(result, **kwargs)
            return SimpleNamespace(run_id="already-recorded", recorded=False)

    links = []
    executor = RecordingSegmentExecutor(
        _Inner(), ReusingSink(),
        on_record=lambda candidate, segment, outcome: links.append(
            (candidate.candidate_id, segment.role, outcome.run_id)
        ),
    )
    executor(_Candidate("c7"), _Segment(SegmentRole.VALIDATION))
    assert links == [("c7", SegmentRole.VALIDATION, "already-recorded")]


def test_failed_linking_is_logged_without_changing_research_result(caplog):
    class RecordedSink(_Sink):
        def record(self, result, **kwargs):
            return SimpleNamespace(run_id="r1")

    def fail(candidate, segment, outcome):
        raise RuntimeError("link persistence failed")

    executor = RecordingSegmentExecutor(_Inner(), RecordedSink(), on_record=fail)
    assert executor(_Candidate(), _Segment(SegmentRole.TEST)).result == "backtest-test"
    assert "link persistence failed" in caplog.text
