"""Session-based train/validation/test split planner.

Splits are expressed and enumerated in exchange **sessions**, never in months
or calendar days. The planner produces a deterministic, validated
:class:`~vibe.research_pipeline.contracts.SplitManifest` per walk-forward fold
plus a single manifest for the untouched final out-of-sample region.

Two-tier design (plan section 8):

    DEV       = train + validation + all walk-forward folds  (may be revisited)
    FINAL_OOS = contiguous, later, disjoint from DEV          (touched once)

Walk-forward folds are generated inside the DEV region only. Selection always
uses a dedicated validation window, never a tail of the training window.

Purge and embargo are *derived* from the strategy's declared horizons and then
*applied* (optionally with conservative padding). The manifest records both. The
derivation rule: purge equals the strategy's maximum label horizon in sessions,
so a strategy that is always flat at the session close (like ORB) has a derived
purge of 0.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vibe.research_pipeline.contracts import (
    SegmentRole,
    SessionSegment,
    SplitManifest,
)
from vibe.research_pipeline.hashing import hash_object
from vibe.research_pipeline.splits.calendar import DEFAULT_CALENDAR, SessionCalendar

__all__ = [
    "PLANNER_VERSION",
    "SplitSpec",
    "SplitPlan",
    "SplitPlanError",
    "TemporalSplitPlanner",
]

PLANNER_VERSION = 1


class SplitPlanError(ValueError):
    """Raised when a specification cannot yield a valid split plan.

    A dedicated type so callers can distinguish an unsatisfiable request (too
    little history, a fold crossing the holdout) from an ordinary bug.
    """


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SplitSpec(_Frozen):
    """Declarative walk-forward split request, counted in sessions."""

    calendar_name: str = Field(DEFAULT_CALENDAR, min_length=1)

    dev_start: date
    dev_end: date
    final_oos_start: date
    final_oos_end: date

    train_sessions: int = Field(..., ge=1)
    validation_sessions: int = Field(..., ge=1)
    test_sessions: int = Field(..., ge=1)
    step_sessions: int = Field(
        ...,
        description=(
            "Sessions the fold window advances each step. Set equal to "
            "test_sessions so test windows tile without overlapping."
        ),
    )

    # Derivation inputs. Purge follows the label horizon; embargo follows
    # serial correlation between adjacent per-session returns; warmup follows
    # the longest indicator lookback (context, not leakage).
    max_label_horizon_sessions: int = Field(0, ge=0)
    serial_correlation_sessions: int = Field(0, ge=0)
    indicator_lookback_sessions: int = Field(0, ge=0)

    # Conservative padding added on top of the derived requirement. Applied is
    # allowed to exceed derived; SplitManifest rejects applied < derived.
    purge_padding_sessions: int = Field(1, ge=0)
    embargo_padding_sessions: int = Field(1, ge=0)

    # Explicit overrides. When set, used verbatim as the applied value so a
    # reviewer can pin an exact number (and so an intentionally-too-small value
    # is rejected loudly rather than silently padded up).
    purge_sessions_applied: int | None = Field(None, ge=0)
    embargo_sessions_applied: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _regions_ordered_and_disjoint(self) -> "SplitSpec":
        if self.dev_end < self.dev_start:
            raise ValueError(
                f"DEV region ends ({self.dev_end}) before it starts "
                f"({self.dev_start})"
            )
        if self.final_oos_end < self.final_oos_start:
            raise ValueError(
                f"FINAL_OOS region ends ({self.final_oos_end}) before it starts "
                f"({self.final_oos_start})"
            )
        return self

    @property
    def purge_sessions_derived(self) -> int:
        return self.max_label_horizon_sessions

    @property
    def embargo_sessions_derived(self) -> int:
        return self.serial_correlation_sessions

    @property
    def applied_purge(self) -> int:
        if self.purge_sessions_applied is not None:
            return self.purge_sessions_applied
        return self.purge_sessions_derived + self.purge_padding_sessions

    @property
    def applied_embargo(self) -> int:
        if self.embargo_sessions_applied is not None:
            return self.embargo_sessions_applied
        return self.embargo_sessions_derived + self.embargo_padding_sessions

    @property
    def spec_hash(self) -> str:
        return hash_object(self)


class SplitPlan(_Frozen):
    """The full, hashable output: every fold plus the final holdout."""

    planner_version: int = PLANNER_VERSION
    calendar_name: str = Field(..., min_length=1)
    spec_hash: str = Field(..., min_length=1)
    folds: tuple[SplitManifest, ...] = Field(..., min_length=1)
    final_oos: SplitManifest

    @property
    def plan_hash(self) -> str:
        return hash_object(self)


class TemporalSplitPlanner:
    """Turns a :class:`SplitSpec` into a validated :class:`SplitPlan`."""

    def __init__(self, calendar: SessionCalendar | None = None) -> None:
        self._calendar = calendar

    def _calendar_for(self, spec: SplitSpec) -> SessionCalendar:
        if self._calendar is not None and self._calendar.name == spec.calendar_name:
            return self._calendar
        return SessionCalendar(spec.calendar_name)

    def plan(self, spec: SplitSpec) -> SplitPlan:
        if spec.step_sessions <= 0:
            raise SplitPlanError(
                f"step_sessions must be positive, got {spec.step_sessions}. A "
                f"non-positive step cannot advance the walk-forward window."
            )
        if spec.final_oos_start <= spec.dev_end:
            raise SplitPlanError(
                f"FINAL_OOS must start after DEV ends: final_oos_start "
                f"({spec.final_oos_start}) <= dev_end ({spec.dev_end}). "
                f"Otherwise a walk-forward test fold would extend into the "
                f"untouched holdout."
            )

        calendar = self._calendar_for(spec)
        dev_sessions = calendar.sessions_between(spec.dev_start, spec.dev_end)

        gap = spec.applied_purge + spec.applied_embargo
        fold_span = (
            spec.train_sessions
            + gap
            + spec.validation_sessions
            + gap
            + spec.test_sessions
        )
        if len(dev_sessions) < fold_span:
            raise SplitPlanError(
                f"DEV region has {len(dev_sessions)} sessions but a single fold "
                f"needs {fold_span} (train {spec.train_sessions} + validation "
                f"{spec.validation_sessions} + test {spec.test_sessions} + "
                f"{2 * gap} purge/embargo). Widen the DEV region or shrink the "
                f"windows."
            )

        folds = tuple(
            self._build_fold(spec, calendar, dev_sessions, start)
            for start in range(0, len(dev_sessions) - fold_span + 1, spec.step_sessions)
        )

        return SplitPlan(
            calendar_name=spec.calendar_name,
            spec_hash=spec.spec_hash,
            folds=folds,
            final_oos=self._build_final_oos(spec, calendar),
        )

    def _build_fold(
        self,
        spec: SplitSpec,
        calendar: SessionCalendar,
        dev: tuple[date, ...],
        start: int,
    ) -> SplitManifest:
        gap = spec.applied_purge + spec.applied_embargo

        train_lo = start
        train_hi = train_lo + spec.train_sessions - 1
        val_lo = train_hi + 1 + gap
        val_hi = val_lo + spec.validation_sessions - 1
        test_lo = val_hi + 1 + gap
        test_hi = test_lo + spec.test_sessions - 1

        segments: list[SessionSegment] = []

        if spec.indicator_lookback_sessions > 0:
            warmup = calendar.sessions_before(
                dev[train_lo], spec.indicator_lookback_sessions
            )
            if len(warmup) < spec.indicator_lookback_sessions:
                raise SplitPlanError(
                    f"Insufficient warmup history: fold starting {dev[train_lo]} "
                    f"needs {spec.indicator_lookback_sessions} prior sessions for "
                    f"indicator priming, only {len(warmup)} exist on "
                    f"{spec.calendar_name}."
                )
            segments.append(
                SessionSegment(
                    role=SegmentRole.WARMUP,
                    start_session=warmup[0],
                    end_session=warmup[-1],
                    session_count=len(warmup),
                )
            )

        segments.append(self._segment(SegmentRole.TRAIN, dev, train_lo, train_hi))
        segments.append(self._segment(SegmentRole.VALIDATION, dev, val_lo, val_hi))
        test_segment = self._segment(SegmentRole.TEST, dev, test_lo, test_hi)

        if test_segment.end_session >= spec.final_oos_start:
            raise SplitPlanError(
                f"Fold test window ends {test_segment.end_session}, which is on "
                f"or after final_oos_start ({spec.final_oos_start}). A test fold "
                f"must never reach into the untouched holdout."
            )
        segments.append(test_segment)

        return self._manifest(spec, tuple(segments))

    def _build_final_oos(
        self, spec: SplitSpec, calendar: SessionCalendar
    ) -> SplitManifest:
        oos = calendar.sessions_between(spec.final_oos_start, spec.final_oos_end)
        if not oos:
            raise SplitPlanError(
                f"FINAL_OOS region {spec.final_oos_start}..{spec.final_oos_end} "
                f"contains no sessions on {spec.calendar_name}."
            )
        segment = SessionSegment(
            role=SegmentRole.FINAL_OOS,
            start_session=oos[0],
            end_session=oos[-1],
            session_count=len(oos),
        )
        return self._manifest(spec, (segment,), warmup_sessions=0)

    @staticmethod
    def _segment(
        role: SegmentRole, dev: tuple[date, ...], lo: int, hi: int
    ) -> SessionSegment:
        count = hi - lo + 1
        if count <= 0:
            raise SplitPlanError(
                f"{role.value} segment resolved to {count} sessions; every "
                f"graded segment must contain at least one session."
            )
        return SessionSegment(
            role=role,
            start_session=dev[lo],
            end_session=dev[hi],
            session_count=count,
        )

    @staticmethod
    def _manifest(
        spec: SplitSpec,
        segments: tuple[SessionSegment, ...],
        *,
        warmup_sessions: int | None = None,
    ) -> SplitManifest:
        if warmup_sessions is None:
            warmup_sessions = spec.indicator_lookback_sessions
        return SplitManifest(
            calendar_name=spec.calendar_name,
            segments=segments,
            purge_sessions_derived=spec.purge_sessions_derived,
            purge_sessions_applied=spec.applied_purge,
            embargo_sessions_derived=spec.embargo_sessions_derived,
            embargo_sessions_applied=spec.applied_embargo,
            warmup_sessions=warmup_sessions,
        )
