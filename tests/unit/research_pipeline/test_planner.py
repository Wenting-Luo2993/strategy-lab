"""Tests for the P3 temporal split planner.

The defect being guarded against is calendar-day fold arithmetic
(``timedelta(days=months * 30)``), which makes nominally-equal folds contain
different numbers of trading sessions and therefore not comparable. Every fold
these tests generate must contain an identical number of graded sessions.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.splits.planner import (
    SplitPlanError,
    SplitSpec,
    TemporalSplitPlanner,
)


def _spec(**overrides) -> SplitSpec:
    defaults = dict(
        calendar_name="XNYS",
        dev_start=date(2018, 1, 1),
        dev_end=date(2021, 12, 31),
        final_oos_start=date(2022, 1, 1),
        final_oos_end=date(2022, 12, 31),
        train_sessions=100,
        validation_sessions=25,
        test_sessions=25,
        step_sessions=25,
        indicator_lookback_sessions=20,
    )
    defaults.update(overrides)
    return SplitSpec(**defaults)


def _plan(**overrides):
    return TemporalSplitPlanner().plan(_spec(**overrides))


def _segment(manifest, role):
    matches = [s for s in manifest.segments if s.role is role]
    assert len(matches) == 1, f"expected exactly one {role} segment"
    return matches[0]


# --------------------------------------------------------------------------
# Fold geometry
# --------------------------------------------------------------------------


def test_plan_generates_multiple_folds():
    plan = _plan()
    assert len(plan.folds) > 1


def test_every_fold_has_the_same_test_session_count():
    """The core defect: calendar-day folds drift; session folds do not.

    Under ``days = months * 30`` different folds would span different numbers of
    trading sessions. Counting in sessions forces every test window to the same
    exact size, which is what makes folds comparable.
    """
    plan = _plan()
    test_counts = {_segment(f, SegmentRole.TEST).session_count for f in plan.folds}
    assert test_counts == {25}

    train_counts = {_segment(f, SegmentRole.TRAIN).session_count for f in plan.folds}
    val_counts = {_segment(f, SegmentRole.VALIDATION).session_count for f in plan.folds}
    assert train_counts == {100}
    assert val_counts == {25}


def test_fold_boundaries_land_on_real_sessions():
    """No boundary may fall on a weekend or exchange holiday."""
    from vibe.research_pipeline.splits.calendar import SessionCalendar

    cal = SessionCalendar("XNYS")
    plan = _plan()
    for fold in plan.folds:
        for segment in fold.segments:
            assert cal.is_session(segment.start_session), segment
            assert cal.is_session(segment.end_session), segment


def test_train_validation_test_are_ordered_and_non_overlapping():
    """Selection uses a dedicated validation window after train, then test."""
    plan = _plan()
    for fold in plan.folds:
        train = _segment(fold, SegmentRole.TRAIN)
        val = _segment(fold, SegmentRole.VALIDATION)
        test = _segment(fold, SegmentRole.TEST)
        assert train.end_session < val.start_session
        assert val.end_session < test.start_session


def test_adjacent_folds_do_not_overlap_in_graded_segments():
    """With step == test window, successive test windows must not overlap."""
    plan = _plan()
    tests = [_segment(f, SegmentRole.TEST) for f in plan.folds]
    for earlier, later in zip(tests, tests[1:]):
        assert later.start_session > earlier.end_session


def test_warmup_precedes_training_and_is_recorded():
    """Warmup is context: it sits before the fold and is never graded."""
    plan = _plan()
    fold = plan.folds[0]
    warmup = _segment(fold, SegmentRole.WARMUP)
    train = _segment(fold, SegmentRole.TRAIN)
    assert warmup.end_session < train.start_session
    assert warmup.session_count == 20
    assert fold.manifest_hash  # constructing it already validated the manifest


# --------------------------------------------------------------------------
# Purge / embargo derivation and recording
# --------------------------------------------------------------------------


def test_orb_derived_purge_and_embargo_are_zero():
    """An always-flat-at-close strategy has derived purge 0; applied pads to 1."""
    plan = _plan(max_label_horizon_sessions=0, serial_correlation_sessions=0)
    fold = plan.folds[0]
    assert fold.purge_sessions_derived == 0
    assert fold.embargo_sessions_derived == 0
    assert fold.purge_sessions_applied == 1
    assert fold.embargo_sessions_applied == 1


def test_multi_session_label_horizon_raises_derived_purge():
    plan = _plan(max_label_horizon_sessions=3, purge_padding_sessions=0)
    fold = plan.folds[0]
    assert fold.purge_sessions_derived == 3
    assert fold.purge_sessions_applied == 3


def test_applied_purge_below_derived_is_rejected():
    """Padding up is fine; applying less purge than derived is leakage."""
    with pytest.raises(ValidationError, match="below the derived requirement"):
        _plan(max_label_horizon_sessions=5, purge_sessions_applied=1)


def test_warmup_may_overlap_earlier_data():
    """A warmup window overlapping earlier graded data is context, not leakage.

    The manifest exempts WARMUP from the overlap check; this asserts the planner
    relies on that rather than rejecting a legitimate priming window.
    """
    from vibe.research_pipeline.contracts import SessionSegment, SplitManifest

    manifest = SplitManifest(
        calendar_name="XNYS",
        segments=(
            SessionSegment(
                role=SegmentRole.WARMUP,
                start_session=date(2020, 12, 1),
                end_session=date(2020, 12, 31),
                session_count=20,
            ),
            SessionSegment(
                role=SegmentRole.TRAIN,
                start_session=date(2020, 12, 15),  # overlaps warmup on purpose
                end_session=date(2021, 6, 30),
                session_count=130,
            ),
            SessionSegment(
                role=SegmentRole.TEST,
                start_session=date(2021, 7, 1),
                end_session=date(2021, 12, 31),
                session_count=128,
            ),
        ),
        purge_sessions_derived=0,
        purge_sessions_applied=1,
        embargo_sessions_derived=0,
        embargo_sessions_applied=1,
        warmup_sessions=20,
    )
    assert manifest.warmup_sessions == 20


# --------------------------------------------------------------------------
# Loud rejections
# --------------------------------------------------------------------------


def test_zero_step_is_rejected():
    with pytest.raises(SplitPlanError, match="step_sessions must be positive"):
        _plan(step_sessions=0)


def test_negative_step_is_rejected():
    with pytest.raises(SplitPlanError, match="step_sessions must be positive"):
        _plan(step_sessions=-5)


def test_dev_region_too_small_for_one_fold_is_rejected():
    with pytest.raises(SplitPlanError, match="needs"):
        _plan(dev_start=date(2021, 11, 1), dev_end=date(2021, 12, 31))


def test_test_window_crossing_into_final_oos_is_rejected():
    """DEV and FINAL_OOS must be disjoint, else a test fold reaches the holdout."""
    with pytest.raises(SplitPlanError, match="untouched holdout"):
        _spec_and_plan_overlapping()


def _spec_and_plan_overlapping():
    spec = _spec(
        dev_start=date(2018, 1, 1),
        dev_end=date(2021, 12, 31),
        final_oos_start=date(2021, 6, 1),  # inside DEV
        final_oos_end=date(2022, 12, 31),
    )
    return TemporalSplitPlanner().plan(spec)


def test_insufficient_warmup_history_is_rejected():
    """A warmup window larger than reachable history cannot be primed."""
    with pytest.raises(SplitPlanError, match="[Ii]nsufficient warmup"):
        _plan(indicator_lookback_sessions=3000)


# --------------------------------------------------------------------------
# Final OOS
# --------------------------------------------------------------------------


def test_final_oos_manifest_is_a_single_untouched_segment():
    plan = _plan()
    final = plan.final_oos
    assert len(final.segments) == 1
    assert final.segments[0].role is SegmentRole.FINAL_OOS
    assert final.segments[0].start_session >= date(2022, 1, 1)


def test_final_oos_never_appears_in_fold_segments():
    plan = _plan()
    for fold in plan.folds:
        assert all(s.role is not SegmentRole.FINAL_OOS for s in fold.segments)


# --------------------------------------------------------------------------
# Determinism and hash sensitivity
# --------------------------------------------------------------------------


def test_same_spec_produces_identical_manifests_and_hashes():
    a = _plan()
    b = _plan()
    assert [f.manifest_hash for f in a.folds] == [f.manifest_hash for f in b.folds]
    assert a.plan_hash == b.plan_hash


def test_one_session_boundary_shift_changes_the_hash():
    """A single-session change in any window must change the plan hash."""
    base = _plan()
    shifted = _plan(test_sessions=26)
    assert base.plan_hash != shifted.plan_hash
    assert base.folds[0].manifest_hash != shifted.folds[0].manifest_hash


def test_spec_hash_is_recorded_on_the_plan():
    spec = _spec()
    plan = TemporalSplitPlanner().plan(spec)
    assert plan.spec_hash == spec.spec_hash
