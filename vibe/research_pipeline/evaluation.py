"""Turn a finished simulation into a real validation decision.

This module supplies the one seam the research pipeline was missing. Every
other piece of plan section 3 exists: the planner, the optimizer, the
selector, the segment executor, the leakage suite, and
:class:`~vibe.research_pipeline.validation.MetricValidator`. What did not
exist was the code that assembles a :class:`ValidationInput` from a real
backtest, so nothing ever called the validator and no run ever left
``VALIDATING``.

The consequences of that omission were larger than they looked:

* Recorded runs came to rest at ``VALIDATING`` forever, so the analysis
  workspace's validated-only default population was permanently empty.
* ``finalize_validation`` is what atomically enqueues a publish summary, so
  nothing was ever published to the remote research store either.

Both followed from the same missing function rather than from any defect in
the components themselves.

**Why the leakage report is injected rather than recomputed per candidate.**
Leakage is a property of the feature and strategy *code*, not of a parameter
value: perturbing a future bar either changes a causal feature or it does not,
and changing a stop distance cannot alter that. Running the suite once per
candidate per fold per role would multiply its cost by the grid size while
producing the identical answer every time. It is therefore computed once per
walk-forward run and attached to every candidate's input. The validator still
checks that the evidence is *complete*: a profile declares
``required_leakage_checks``, and ``leakage_passed`` is false when any of them
is absent, so an injected report cannot launder a check that never ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping, Optional

import pandas as pd

from vibe.research_pipeline.contracts import RetentionProfile, SessionSegment
from vibe.research_pipeline.features.leakage import LeakageReport
from vibe.research_pipeline.features.registry import registry_hash
from vibe.research_pipeline.recording import build_run_evidence
from vibe.research_pipeline.validation import (
    DataIntegritySummary,
    MetricValidator,
    ValidationInput,
    ValidationProfile,
    ValidationReport,
    ValidationScope,
)

__all__ = [
    "BarIntegrityCounts",
    "SegmentValidator",
    "build_run_validator",
    "build_validation_input",
    "measure_bar_integrity",
    "measure_data_integrity",
    "slice_graded_bars",
]


@dataclass(frozen=True)
class BarIntegrityCounts:
    """Defects counted directly from the bars a segment consumed."""

    bar_count: int = 0
    duplicate_timestamps: int = 0
    non_monotonic_timestamps: int = 0
    invalid_ohlc_bars: int = 0
    non_positive_price_bars: int = 0
    anomalous_sessions: int = 0


def _timestamps(bars: pd.DataFrame) -> Optional[pd.Series]:
    if isinstance(bars.index, pd.DatetimeIndex):
        return pd.Series(bars.index)
    for column in ("timestamp", "datetime", "time", "date"):
        if column in bars.columns:
            return pd.to_datetime(bars[column])
    return None


def measure_bar_integrity(
    bars: pd.DataFrame,
    *,
    anomaly_tolerance: float = 0.5,
) -> BarIntegrityCounts:
    """Count data defects in the bars of one graded segment.

    ``anomaly_tolerance`` flags a session whose bar count differs from the
    median session by more than this fraction. A half-day holiday session is a
    real anomaly for comparison purposes even though the data is not corrupt,
    which is why the resulting finding is surfaced rather than suppressed.
    """
    if bars.empty:
        return BarIntegrityCounts()

    stamps = _timestamps(bars)
    duplicates = 0
    non_monotonic = 0
    anomalous = 0
    if stamps is not None:
        duplicates = int(stamps.duplicated().sum())
        non_monotonic = int((stamps.diff().dropna() < pd.Timedelta(0)).sum())
        per_session = stamps.dt.date.value_counts()
        if len(per_session) > 1:
            median = float(per_session.median())
            if median > 0:
                deviation = (per_session - median).abs() / median
                anomalous = int((deviation > anomaly_tolerance).sum())

    invalid = 0
    non_positive = 0
    ohlc = [c for c in ("open", "high", "low", "close") if c in bars.columns]
    if {"high", "low"} <= set(ohlc):
        invalid = int((bars["high"] < bars["low"]).sum())
    if ohlc:
        non_positive = int((bars[ohlc] <= 0).any(axis=1).sum())

    return BarIntegrityCounts(
        bar_count=int(len(bars)),
        duplicate_timestamps=duplicates,
        non_monotonic_timestamps=non_monotonic,
        invalid_ohlc_bars=invalid,
        non_positive_price_bars=non_positive,
        anomalous_sessions=anomalous,
    )


def slice_graded_bars(
    bars: pd.DataFrame, start_session: date, end_session: date
) -> pd.DataFrame:
    """Restrict ``bars`` to the graded window, excluding warmup.

    Warmup bars are deliberately dropped: they are loaded to prime indicators
    and are excluded from every metric, so counting their defects against a
    segment would attribute a problem to a window that never graded them.
    """
    stamps = _timestamps(bars)
    if stamps is None:
        return bars
    sessions = stamps.dt.date.to_numpy()
    mask = (sessions >= start_session) & (sessions <= end_session)
    return bars.loc[mask.tolist()]


def measure_data_integrity(
    result: Any,
    *,
    expected_sessions: int,
    bars: Optional[pd.DataFrame] = None,
) -> DataIntegritySummary:
    """Summarize integrity of the data one graded segment actually consumed.

    ``observed_sessions`` is taken from the metrics rather than recounted, so
    the validator's DATA-007 cross-check (metric sessions must agree with
    observed sessions) compares the metric layer against itself and cannot be
    made to agree by construction here.

    ``expected_sessions`` comes from the exchange calendar. A mismatch is
    blocking on purpose: it means the calendar and the dataset disagree about
    which days exist, and every fold-to-fold comparison silently assumes they
    do not.
    """
    observed = int(getattr(result.equity, "n_sessions", 0) or 0)

    if bars is not None:
        counts = measure_bar_integrity(bars)
    else:
        per_session = float(getattr(result.equity, "bars_per_session", 0.0) or 0.0)
        counts = BarIntegrityCounts(bar_count=int(round(observed * per_session)))

    return DataIntegritySummary(
        expected_sessions=int(expected_sessions),
        observed_sessions=observed,
        bar_count=counts.bar_count,
        duplicate_timestamps=counts.duplicate_timestamps,
        non_monotonic_timestamps=counts.non_monotonic_timestamps,
        invalid_ohlc_bars=counts.invalid_ohlc_bars,
        non_positive_price_bars=counts.non_positive_price_bars,
        anomalous_sessions=counts.anomalous_sessions,
    )


def build_validation_input(
    result: Any,
    *,
    starting_equity: float,
    expected_sessions: int,
    leakage_report: LeakageReport,
    scope: ValidationScope = ValidationScope.RUN,
    bars: Optional[pd.DataFrame] = None,
    registry_hash_at_execution: Optional[str] = None,
    retention: RetentionProfile = RetentionProfile.SUMMARY,
) -> ValidationInput:
    """Assemble every piece of evidence one validation decision needs."""
    current = registry_hash()
    return ValidationInput(
        result=result,
        evidence=build_run_evidence(
            result, starting_equity=starting_equity, retention=retention
        ),
        data_integrity=measure_data_integrity(
            result, expected_sessions=expected_sessions, bars=bars
        ),
        leakage_report=leakage_report,
        registry_hash_at_execution=registry_hash_at_execution or current,
        current_registry_hash=current,
        scope=scope,
    )


class SegmentValidator:
    """Adapt :class:`MetricValidator` to the ``CandidateValidator`` protocol.

    ``Optimizer`` and ``Selector`` call this with every candidate they run, so
    a candidate that fails a hard invariant is excluded from selection rather
    than silently competing with sound ones.
    """

    def __init__(
        self,
        profile: ValidationProfile,
        leakage_report: LeakageReport,
        *,
        starting_equity: float,
        bars_by_symbol: Optional[Mapping[str, pd.DataFrame]] = None,
        registry_hash_at_execution: Optional[str] = None,
        leakage_by_segment: Optional[Mapping[Any, LeakageReport]] = None,
    ) -> None:
        self._validator = MetricValidator(profile)
        self._leakage_report = leakage_report
        self._starting_equity = float(starting_equity)
        self._bars_by_symbol = dict(bars_by_symbol or {})
        self._registry_hash_at_execution = registry_hash_at_execution
        self._leakage_by_segment = dict(leakage_by_segment or {})

    @staticmethod
    def segment_key(segment: SessionSegment) -> tuple[str, date, date]:
        """Identity of a segment for per-fold leakage lookup.

        Role plus bounds, because split contamination is a property of the
        *fold* rather than of the code: it asks whether this fold's test
        sessions leaked into this fold's optimization inputs, and the answer
        differs fold to fold.
        """
        return (segment.role.value, segment.start_session, segment.end_session)

    def _leakage_for(self, segment: SessionSegment) -> LeakageReport:
        supplement = self._leakage_by_segment.get(self.segment_key(segment))
        if supplement is None:
            return self._leakage_report
        return LeakageReport(
            self._leakage_report.findings + supplement.findings
        )

    def __call__(
        self,
        candidate: Any,
        segment_result: Any,
        scope: ValidationScope,
    ) -> ValidationReport:
        segment: SessionSegment = segment_result.segment
        return self.validate_result(segment_result.result, segment, scope)

    def validate_result(
        self,
        result: Any,
        segment: SessionSegment,
        scope: ValidationScope,
    ) -> ValidationReport:
        """Validate a bare backtest result against its known segment."""
        bars = self._graded_bars(result, segment)
        return self._validator.validate(
            build_validation_input(
                result,
                starting_equity=self._starting_equity,
                expected_sessions=segment.session_count,
                leakage_report=self._leakage_for(segment),
                scope=scope,
                bars=bars,
                registry_hash_at_execution=self._registry_hash_at_execution,
            )
        )

    def for_recorded_segments(
        self,
        segments: Iterable[SessionSegment],
        *,
        scope: ValidationScope = ValidationScope.CANDIDATE,
    ) -> Callable[[Any], ValidationReport]:
        """Bind bare recorded results back to their declared split segments.

        ``ResearchRunSink`` intentionally receives only the underlying
        ``BacktestResult``. Nested walk-forward validation additionally needs
        the segment role and bounds so it can use the right expected session
        count, graded bars, and per-fold contamination evidence. The graded
        result window is a stable, persisted identity for that lookup.
        """
        by_window: dict[tuple[date, date], SessionSegment] = {}
        for segment in segments:
            key = (segment.start_session, segment.end_session)
            existing = by_window.get(key)
            if existing is not None and existing != segment:
                raise ValueError(
                    "Cannot bind recorded validation to ambiguous segment "
                    f"window {key[0]}..{key[1]}"
                )
            by_window[key] = segment

        def validate(result: Any) -> ValidationReport:
            start = _result_session(getattr(result, "start_date", None), "start")
            end = _result_session(getattr(result, "end_date", None), "end")
            segment = by_window.get((start, end))
            if segment is None:
                raise ValueError(
                    "Recorded result window is not present in the split plan: "
                    f"{start}..{end}"
                )
            return self.validate_result(result, segment, scope)

        return validate

    def _graded_bars(
        self, result: Any, segment: SessionSegment
    ) -> Optional[pd.DataFrame]:
        symbol = getattr(result, "symbol", None)
        frame = self._bars_by_symbol.get(symbol) if symbol else None
        if frame is None:
            return None
        return slice_graded_bars(frame, segment.start_session, segment.end_session)


def _result_session(value: Any, which: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(
                f"Invalid recorded result {which} date: {value!r}"
            ) from exc
    raise ValueError(f"Recorded result has no usable {which} date: {value!r}")


def build_run_validator(
    profile: ValidationProfile,
    leakage_report: LeakageReport,
    *,
    starting_equity: float,
    expected_sessions_for: Callable[[Any], int],
    bars_for: Optional[Callable[[Any], Optional[pd.DataFrame]]] = None,
    scope: ValidationScope = ValidationScope.RUN,
    registry_hash_at_execution: Optional[str] = None,
) -> Callable[[Any], ValidationReport]:
    """Build the ``validator`` callable ``RunRecorder`` accepts.

    This is what lets a recorded run leave ``VALIDATING``. It is deliberately
    a *factory over supplied evidence* rather than a convenience that invents
    evidence: the caller must say how many sessions the exchange calendar
    expects and, where it can, which bars the run consumed. A caller holding
    no leakage evidence passes an empty report and its runs will not reach
    ``COMPLETED``, because absent required checks are blocking -- which is the
    correct outcome, not an inconvenience to design around.
    """
    validator = MetricValidator(profile)

    def validate(result: Any) -> ValidationReport:
        return validator.validate(
            build_validation_input(
                result,
                starting_equity=starting_equity,
                expected_sessions=expected_sessions_for(result),
                leakage_report=leakage_report,
                scope=scope,
                bars=bars_for(result) if bars_for is not None else None,
                registry_hash_at_execution=registry_hash_at_execution,
            )
        )

    return validate
