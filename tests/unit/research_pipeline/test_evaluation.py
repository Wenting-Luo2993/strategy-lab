"""Tests for the evaluation seam that makes ``MetricValidator`` reachable.

Each test here is written so that it fails if the guarantee it describes is
removed. That matters more than usual for this module: the gap audit found
that every validated component existed and was correct while nothing called
any of them, so tests that merely assert a function returns something would
have passed just as happily against the orphaned pipeline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from vibe.research_pipeline.evaluation import (
    measure_bar_integrity,
    measure_data_integrity,
    slice_graded_bars,
)


def _bars(start: str, periods: int, freq: str = "5min") -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq=freq, tz="America/New_York")
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000,
        },
        index=index,
    )


class TestBarIntegrity:
    def test_clean_bars_report_no_anomalies(self):
        counts = measure_bar_integrity(_bars("2022-01-03 09:30", 78))
        assert counts.duplicate_timestamps == 0
        assert counts.non_monotonic_timestamps == 0
        assert counts.invalid_ohlc_bars == 0
        assert counts.non_positive_price_bars == 0

    def test_duplicate_timestamps_are_counted(self):
        bars = _bars("2022-01-03 09:30", 10)
        bars = pd.concat([bars, bars.iloc[[3]]]).sort_index()
        assert measure_bar_integrity(bars).duplicate_timestamps == 1

    def test_non_monotonic_timestamps_are_counted(self):
        bars = _bars("2022-01-03 09:30", 10)
        index = list(bars.index)
        index[4], index[5] = index[5], index[4]
        bars.index = pd.DatetimeIndex(index)
        assert measure_bar_integrity(bars).non_monotonic_timestamps > 0

    def test_high_below_low_is_an_invalid_price(self):
        bars = _bars("2022-01-03 09:30", 10)
        bars.iloc[4, bars.columns.get_loc("high")] = 1.0
        assert measure_bar_integrity(bars).invalid_ohlc_bars == 1

    def test_non_positive_price_is_invalid(self):
        bars = _bars("2022-01-03 09:30", 10)
        bars.iloc[2, bars.columns.get_loc("low")] = 0.0
        assert measure_bar_integrity(bars).non_positive_price_bars == 1

    def test_a_short_session_is_flagged_as_anomalous(self):
        """A truncated session is a data defect, not a quiet rounding error.

        Half-days and partial downloads both look like this, and both change
        the number of opportunities a strategy had. Counting them keeps the
        difference visible instead of letting it be absorbed into the metrics.
        """
        bars = pd.concat(
            [
                _bars("2022-01-03 09:30", 78),
                _bars("2022-01-04 09:30", 78),
                _bars("2022-01-05 09:30", 78),
                _bars("2022-01-06 09:30", 10),
            ]
        )
        assert measure_bar_integrity(bars).anomalous_sessions == 1

    def test_timestamp_column_is_accepted_instead_of_an_index(self):
        bars = _bars("2022-01-03 09:30", 10).reset_index(names="timestamp")
        assert measure_bar_integrity(bars).duplicate_timestamps == 0


class TestSliceGradedBars:
    def test_warmup_bars_are_excluded_from_the_graded_window(self):
        """Warmup defects must not be attributed to the graded segment.

        Warmup exists to prime indicators, so its bars are deliberately
        outside the evaluated window. Counting their anomalies would let a
        bad priming period fail an otherwise sound fold.
        """
        bars = pd.concat(
            [_bars("2022-01-03 09:30", 78), _bars("2022-01-04 09:30", 78)]
        )
        graded = slice_graded_bars(bars, date(2022, 1, 4), date(2022, 1, 4))
        assert len(graded) == 78
        assert graded.index.min().date() == date(2022, 1, 4)

    def test_the_end_session_is_inclusive(self):
        bars = pd.concat(
            [_bars("2022-01-03 09:30", 12), _bars("2022-01-04 09:30", 12)]
        )
        graded = slice_graded_bars(bars, date(2022, 1, 3), date(2022, 1, 4))
        assert len(graded) == 24


class _Equity:
    def __init__(self, n_sessions: int) -> None:
        self.n_sessions = n_sessions


class _Result:
    def __init__(self, n_sessions: int) -> None:
        self.equity = _Equity(n_sessions)


class TestDataIntegrity:
    def test_observed_sessions_come_from_the_result_not_the_expectation(self):
        """The cross-check must be able to disagree.

        DATA-001 compares the sessions the calendar says exist against the
        sessions the backtest actually graded. Deriving both from the same
        number would make the check structurally incapable of failing, which
        is the failure mode this test exists to prevent.
        """
        summary = measure_data_integrity(_Result(37), expected_sessions=40)
        assert summary.expected_sessions == 40
        assert summary.observed_sessions == 37

    def test_bar_defects_are_carried_into_the_summary(self):
        bars = _bars("2022-01-03 09:30", 10)
        bars.iloc[1, bars.columns.get_loc("high")] = 0.5
        summary = measure_data_integrity(
            _Result(1), expected_sessions=1, bars=bars
        )
        assert summary.invalid_ohlc_bars == 1


class TestSegmentKey:
    def test_key_separates_folds_that_share_a_role(self):
        """Split contamination is per-fold, so its key must be too.

        Every fold has a TEST segment. Keying on role alone would let one
        fold's contamination evidence answer for another fold's, which is
        exactly the kind of silent reuse the audit was written about.
        """
        from vibe.research_pipeline.contracts import SegmentRole, SessionSegment
        from vibe.research_pipeline.evaluation import SegmentValidator

        first = SessionSegment(
            role=SegmentRole.TEST,
            start_session=date(2022, 3, 1),
            end_session=date(2022, 3, 31),
            session_count=22,
        )
        second = SessionSegment(
            role=SegmentRole.TEST,
            start_session=date(2022, 4, 1),
            end_session=date(2022, 4, 29),
            session_count=20,
        )
        assert SegmentValidator.segment_key(first) != SegmentValidator.segment_key(
            second
        )
