"""Assemble the evidence a recorded run needs in order to be validated.

A run recorded without a validator rests at ``VALIDATING`` forever. That was
the state of every run in the store: the validation machinery existed, was
correct, and was unit-tested, but nothing supplied it with evidence, so
nothing ever left the holding state.

This module is the supply side. It lives on the backtester side of the seam
for the same reason :mod:`vibe.backtester.analysis.research_sink` does --
gathering the evidence requires the feature engine and the parquet layout, and
having ``research_pipeline`` reach back for those would invert the dependency.

The evidence is deliberately *measured*, never asserted. A helper that
returned an empty :class:`LeakageReport` to make validation pass would be
worse than no helper at all, because the resulting ``COMPLETED`` run would
carry the authority of a check that never ran.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

__all__ = [
    "EXCHANGE_TZ",
    "build_leakage_report",
    "build_orb_boundary_report",
    "build_study_leakage_report",
    "build_study_validator",
]

#: Bar bounds must be exchange-local. A session is identified by its
#: exchange-local calendar date, so expressing the bounds in UTC would shift
#: the session boundary by the UTC offset and silently clip a session.
EXCHANGE_TZ = ZoneInfo("America/New_York")


def load_session_bars(
    symbol: str,
    data_dir: Path,
    start: date,
    end: date,
    timeframe: str = "5min",
) -> Any:
    """Load and resample the bars a study consumed, on exchange-local bounds."""
    from vibe.backtester.core.engine import _resample
    from vibe.backtester.data.parquet_loader import ParquetLoader

    loader = ParquetLoader(data_dir, [symbol])
    bars_1m = asyncio.run(
        loader.get_bars(
            symbol,
            start_time=datetime.combine(start, datetime.min.time(), tzinfo=EXCHANGE_TZ),
            end_time=datetime.combine(end, datetime.max.time(), tzinfo=EXCHANGE_TZ),
        )
    )
    return _resample(bars_1m, timeframe)


def build_leakage_report(bars) -> Any:
    """Run the run-wide leakage suite against the research precompute path.

    Once per run, not per candidate: leakage is a property of the feature
    code, and no parameter value can make a causal feature peek. Running it
    per candidate would multiply cost by the grid size for an identical
    answer.

    The callable under test must be the research precompute step, not the
    engine. The engine is downstream of any contamination, so pointing the
    suite at it produces a green report that means nothing.
    """
    from vibe.backtester.analysis.regime_research.features import FeatureEngine
    from vibe.research_pipeline.features.leakage import run_leakage_suite
    from vibe.research_pipeline.features.registry import SWEEP_PRECOMPUTED_FEATURES

    engine = FeatureEngine()

    def compute(frame):
        return engine.compute(frame, features=list(SWEEP_PRECOMPUTED_FEATURES))

    return run_leakage_suite(
        bars,
        compute,
        cut=max(1, len(bars) // 2),
        features=list(SWEEP_PRECOMPUTED_FEATURES),
    )


def build_orb_boundary_report(bars, ruleset_config: dict) -> Any:
    """Check the opening range never contains the bar that breaks it.

    Driven off the ruleset's own window definition and the real bar grid, so
    it fails if the configured ORB duration does not align with the bar
    interval -- the case where the breakout bar is still inside the range it
    is tested against and the signal measures itself.
    """
    import pandas as pd

    from vibe.research_pipeline.features.leakage import (
        LeakageFinding,
        LeakageReport,
        check_orb_boundary,
    )

    strategy = ruleset_config.get("strategy", {})
    start_clock = str(strategy.get("orb_start_time", "09:30"))
    duration = int(strategy.get("orb_duration_minutes", 5))

    stamps = bars.index
    sessions = pd.Series(stamps).dt.date
    findings: list = []

    # One representative session is enough: the window geometry is identical
    # every day, so a second session re-tests the same arithmetic.
    for session in list(dict.fromkeys(sessions))[:1]:
        day = bars.loc[sessions.to_numpy() == session]
        if day.empty:
            continue
        hour, minute = (int(part) for part in start_clock.split(":")[:2])
        session_open = pd.Timestamp(
            datetime.combine(session, datetime.min.time()), tz=stamps.tz
        ).replace(hour=hour, minute=minute)
        or_end = session_open + pd.Timedelta(minutes=duration)

        window = day.loc[(day.index >= session_open) & (day.index < or_end)]
        after = day.loc[day.index >= or_end]
        if window.empty or after.empty:
            findings.append(
                LeakageFinding(
                    "orb_boundary",
                    "window",
                    False,
                    f"Session {session} has no bars in the opening range "
                    f"{session_open}..{or_end}; the boundary cannot be checked.",
                )
            )
            continue

        return check_orb_boundary(
            day,
            float(window["high"].max()),
            float(window["low"].min()),
            session_open=session_open,
            or_end=or_end,
            breakout_time=after.index[0],
        )

    return LeakageReport(tuple(findings))


def build_study_leakage_report(
    symbol: str,
    data_dir: Path,
    start: date,
    end: date,
    ruleset_config: dict,
    *,
    train_sessions: Optional[Any] = None,
    test_sessions: Optional[Any] = None,
    selection_sessions: Optional[Any] = None,
) -> Tuple[Any, Any]:
    """Gather every leakage check a :class:`ValidationProfile` requires.

    ``ValidationProfile.required_leakage_checks`` names six checks. Four come
    from ``run_leakage_suite``; ``orb_boundary`` and ``split_contamination``
    are separate calls. A missing required check is *blocking*, not permissive,
    so a partial report leaves the run short of ``COMPLETED`` -- which is why
    this gathers all six rather than whichever are convenient.

    A study with no train/test split (a plain sweep) still needs a
    ``split_contamination`` finding. Passing empty session sets is honest
    there: with no split, there is nothing to contaminate, and the check says
    exactly that rather than being silently omitted.

    Returns:
        ``(report, bars)`` -- the bars are returned because callers need them
        for data-integrity measurement and re-loading them would be wasteful.
    """
    from vibe.research_pipeline.features.leakage import (
        LeakageReport,
        check_split_contamination,
    )

    bars = load_session_bars(symbol, data_dir, start, end)

    suite = build_leakage_report(bars)
    boundary = build_orb_boundary_report(bars, ruleset_config)
    contamination = check_split_contamination(
        train_sessions=tuple(train_sessions or ()),
        test_sessions=tuple(test_sessions or ()),
        selection_sessions=selection_sessions,
    )

    merged = LeakageReport(
        tuple(suite.findings) + tuple(boundary.findings) + tuple(contamination.findings)
    )
    return merged, bars


def build_study_validator(
    *,
    symbol: str,
    data_dir: Path,
    start: date,
    end: date,
    ruleset_config: dict,
    starting_equity: float,
    profile: Any = None,
    calendar_name: str = "XNYS",
    scope: Any = None,
) -> Callable[[Any], Any]:
    """Build the ``validator`` a :class:`BacktestRunSink` accepts for a study.

    This is what finally lets a recorded run leave ``VALIDATING``. It measures
    its own evidence rather than taking it on trust, so a study whose features
    do leak produces blocking findings and its runs stay unfinished -- the
    correct outcome.

    ``expected_sessions_for`` is answered from the exchange calendar rather
    than from the data, so a dataset missing sessions disagrees with the
    calendar and raises ``DATA-001`` instead of quietly validating against
    its own gaps.

    The scope defaults to ``CANDIDATE``, not ``RUN``. ``MetricValidator`` only
    applies the plausibility and research-acceptance gates under a candidate
    scope, and the store refuses to complete a run whose report skipped any
    category -- so a ``RUN``-scoped report can never leave ``VALIDATING``.
    That is the right default anyway: each point in a study is a candidate
    parameterization, and the gates that ask whether a result is too good to
    believe are exactly the ones worth applying before it can be selected.
    """
    from vibe.research_pipeline.evaluation import build_run_validator
    from vibe.research_pipeline.splits.calendar import SessionCalendar
    from vibe.research_pipeline.validation import ValidationProfile, ValidationScope

    report, bars = build_study_leakage_report(
        symbol, data_dir, start, end, ruleset_config
    )

    calendar = SessionCalendar(calendar_name)
    expected = len(calendar.sessions_between(start, end))

    return build_run_validator(
        profile or ValidationProfile(),
        report,
        starting_equity=starting_equity,
        expected_sessions_for=lambda _result: expected,
        bars_for=lambda _result: bars,
        scope=scope or ValidationScope.CANDIDATE,
    )
