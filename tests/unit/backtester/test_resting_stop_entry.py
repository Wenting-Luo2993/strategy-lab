"""EntryFillPolicy.RESTING_STOP on synthetic 1-minute sessions.

Every session has the opening range 09:30-09:34 at high 101 / low 99 and sits
quietly at 100 otherwise; each test overrides a few minutes to stage a
breakout. Entry slippage is the engine default of 2 ticks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import (
    EntryFillPolicy,
    ExecutionRealismConfig,
)
from vibe.backtester.core.resting_stop import SessionMinutes, split_sessions
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.common.ruleset.support import UnsupportedRulesetSettingsError

ET = ZoneInfo("America/New_York")
SYMBOL = "TEST"
DAY = (2024, 1, 2)
SLIP = 0.02
_RULESET = Path(__file__).resolve().parents[3] / "vibe" / "rulesets" / "orb_production.yaml"


def _ts(hhmm: str, day=DAY) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime(*day, h, m, tzinfo=ET))


def _session(overrides: dict[str, tuple[float, float, float, float]] | None = None,
             *, after: tuple[str, float] | None = None, day=DAY) -> pd.DataFrame:
    """One session of 1-minute bars, 09:30-15:59.

    ``after=("HH:MM", p)`` parks every minute from that time on at ``p``
    (inside a 0.1 band); ``overrides`` then sets exact (o, h, l, c) rows.
    """
    idx = pd.date_range(_ts("09:30", day), _ts("15:59", day), freq="1min")
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 10_000.0},
        index=idx,
    )
    df.loc[_ts("09:31", day), ["high"]] = 101.0
    df.loc[_ts("09:33", day), ["low"]] = 99.0
    if after is not None:
        start, p = after
        mask = df.index >= _ts(start, day)
        df.loc[mask, ["open", "close"]] = p
        df.loc[mask, "high"] = p + 0.05
        df.loc[mask, "low"] = p - 0.05
    for hhmm, (o, h, l, c) in (overrides or {}).items():
        df.loc[_ts(hhmm, day), ["open", "high", "low", "close"]] = [o, h, l, c]
    df.index.name = "ts_event"
    return df


def _ruleset(**strategy) -> StrategyRuleSet:
    cfg = yaml.safe_load(_RULESET.read_text(encoding="utf-8"))
    cfg["instruments"]["symbols"] = [SYMBOL]
    cfg["strategy"].update(strategy)
    return StrategyRuleSet(**cfg)


def _run(tmp_path, frame, *, ruleset=None, policy=EntryFillPolicy.RESTING_STOP):
    frame.to_parquet(tmp_path / f"{SYMBOL}.parquet")
    engine = BacktestEngine(
        ruleset or _ruleset(),
        data_dir=tmp_path,
        execution_realism=ExecutionRealismConfig.realistic(entry_fill_policy=policy),
    )
    first, last = frame.index[0], frame.index[-1]
    return engine.run(
        SYMBOL,
        datetime(first.year, first.month, first.day, tzinfo=ET),
        datetime(last.year, last.month, last.day, 23, 59, tzinfo=ET),
    )


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------

def test_policy_is_opt_in_and_part_of_identity():
    default = ExecutionRealismConfig.realistic()
    resting = ExecutionRealismConfig.realistic(entry_fill_policy="resting_stop")

    assert default.entry_fill_policy is EntryFillPolicy.AT_NEXT_BAR_OPEN
    assert default == ExecutionRealismConfig.realistic(
        entry_fill_policy=EntryFillPolicy.AT_NEXT_BAR_OPEN
    )
    assert resting == ExecutionRealismConfig.realistic_resting_stop()
    assert resting.identity()["entry_fill_policy"] == "resting_stop"
    assert resting.identity() != default.identity()


def test_completed_bar_filters_fail_loudly(tmp_path):
    cfg = yaml.safe_load(_RULESET.read_text(encoding="utf-8"))
    cfg["instruments"]["symbols"] = [SYMBOL]
    cfg["trade_filter"]["volume_confirmation"] = True
    with pytest.raises(UnsupportedRulesetSettingsError, match="volume_confirmation"):
        BacktestEngine(
            StrategyRuleSet(**cfg),
            data_dir=tmp_path,
            execution_realism=ExecutionRealismConfig.realistic_resting_stop(),
        )
    with pytest.raises(UnsupportedRulesetSettingsError, match="breakout_evaluation"):
        BacktestEngine(
            _ruleset(breakout_evaluation="body"),
            data_dir=tmp_path,
            execution_realism=ExecutionRealismConfig.realistic_resting_stop(),
        )


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def test_long_fills_at_trigger_on_first_touching_minute(tmp_path):
    frame = _session({"09:37": (100.5, 101.3, 100.4, 101.2)}, after=("09:38", 101.2))

    result = _run(tmp_path, frame)

    (trade,) = result.trades
    assert trade.side == "buy"
    assert trade.entry_time == _ts("09:37")
    assert trade.entry_price == pytest.approx(101.0 + SLIP)
    diag = result.execution_diagnostics
    assert diag["resting_stop_triggers"] == 1
    assert diag["resting_stop_gap_fills"] == 0
    assert diag["entries_skipped_ambiguous_trigger"] == 0
    assert diag["entries_skipped_no_1m_data"] == 0


def test_short_fills_at_trigger(tmp_path):
    frame = _session({"09:41": (99.5, 99.6, 98.7, 98.8)}, after=("09:42", 98.8))

    (trade,) = _run(tmp_path, frame).trades

    assert trade.side == "sell"
    assert trade.entry_time == _ts("09:41")
    assert trade.entry_price == pytest.approx(99.0 - SLIP)


def test_gap_open_beyond_trigger_fills_at_the_open(tmp_path):
    frame = _session({"09:36": (101.5, 101.8, 101.4, 101.7)}, after=("09:37", 101.7))

    result = _run(tmp_path, frame)

    (trade,) = result.trades
    assert trade.entry_price == pytest.approx(101.5 + SLIP)
    assert result.execution_diagnostics["resting_stop_gap_fills"] == 1


def test_both_sides_in_one_minute_skips_the_session(tmp_path):
    frame = _session({
        "09:36": (100.0, 101.2, 98.8, 100.0),
        # A clean breakout later the same day must not be taken either.
        "10:30": (100.5, 101.5, 100.4, 101.4),
    })

    result = _run(tmp_path, frame)

    assert result.trades == []
    assert result.execution_diagnostics["entries_skipped_ambiguous_trigger"] == 1


def test_no_entry_at_or_after_cutoff(tmp_path):
    late = _session({"10:00": (100.5, 101.3, 100.4, 101.2)}, after=("10:01", 101.2))
    assert _run(tmp_path, late, ruleset=_ruleset(entry_cutoff_time="10:00")).trades == []

    early = _session({"09:59": (100.5, 101.3, 100.4, 101.2)}, after=("10:00", 101.2))
    (trade,) = _run(tmp_path, early, ruleset=_ruleset(entry_cutoff_time="10:00")).trades
    assert trade.entry_time == _ts("09:59")


def test_completed_bar_body_filter_is_not_applied(tmp_path):
    """The 09:35 bar spikes through OR high but closes back at 100.

    Its body is ~0% of its range, so the 50% body filter rejects it on the
    completed bar -- information a resting stop does not have when it fills.
    """
    frame = _session({"09:36": (100.0, 101.3, 99.9, 100.0)})

    resting = _run(tmp_path, frame)
    (trade,) = resting.trades
    assert trade.entry_time == _ts("09:36")
    assert resting.execution_diagnostics["orb_body_pct_filter_ignored"] == 0.5

    assert _run(tmp_path, frame, policy=EntryFillPolicy.AT_NEXT_BAR_OPEN).trades == []


# ---------------------------------------------------------------------------
# Exits inside the entry's 5-minute bar
# ---------------------------------------------------------------------------

def test_stop_after_fill_in_the_same_5min_bar(tmp_path):
    frame = _session(
        {
            "09:36": (100.5, 101.3, 100.4, 101.2),
            "09:38": (100.0, 100.1, 98.9, 99.0),
        },
        after=("09:39", 99.5),
    )

    result = _run(tmp_path, frame)

    (trade,) = result.trades
    assert trade.entry_time == _ts("09:36")
    assert trade.exit_reason == "STOP"
    assert trade.exit_time == _ts("09:38")
    assert result.execution_diagnostics["resting_stop_entry_bar_exits"] == 1


def test_minutes_before_the_fill_cannot_stop_the_position(tmp_path):
    """09:35 dips below the stop *before* the 09:37 long fill in the same bar.

    The 5-minute bar's low is under the stop, but that part of the bar
    predates the position; only 09:38-09:39 may be checked.
    """
    frame = _session(
        {
            "09:35": (100.0, 100.1, 98.9, 99.5),
            "09:37": (100.5, 101.3, 100.4, 101.2),
        },
        after=("09:38", 101.2),
    )

    result = _run(tmp_path, frame, ruleset=_ruleset(direction_mode="long_only"))

    (trade,) = result.trades
    assert trade.entry_time == _ts("09:37")
    assert trade.exit_reason != "STOP"


# ---------------------------------------------------------------------------
# Missing 1-minute data
# ---------------------------------------------------------------------------

def test_session_without_minute_bars_is_skipped_and_counted(tmp_path):
    minute_day = _session({"09:37": (100.5, 101.3, 100.4, 101.2)}, after=("09:38", 101.2))
    coarse = _session(
        {"09:35": (100.5, 101.3, 100.4, 101.2)}, after=("09:40", 101.2), day=(2024, 1, 3)
    )
    coarse = coarse[coarse.index.minute % 5 == 0]
    frame = pd.concat([minute_day, coarse])

    result = _run(tmp_path, frame)

    assert len(result.trades) == 1
    assert result.trades[0].entry_time == _ts("09:37")
    assert result.execution_diagnostics["entries_skipped_no_1m_data"] == 1


def test_resting_stop_keys_absent_for_other_policies(tmp_path):
    frame = _session({"09:37": (100.5, 101.3, 100.4, 101.2)}, after=("09:38", 101.2))

    diag = _run(tmp_path, frame, policy=EntryFillPolicy.AT_NEXT_BAR_OPEN).execution_diagnostics

    assert "entries_skipped_ambiguous_trigger" not in diag
    assert "entries_skipped_no_1m_data" not in diag


# ---------------------------------------------------------------------------
# SessionMinutes
# ---------------------------------------------------------------------------

def test_session_minutes_trigger_and_resolution():
    frame = _session({"09:37": (101.4, 101.6, 101.3, 101.5)})
    minutes = split_sessions(frame)[pd.Timestamp(datetime(*DAY)).date()]

    assert minutes.minute_resolution
    assert minutes.opening_range(_ts("09:30"), _ts("09:35")) == (101.0, 99.0)
    lo, hi = minutes.span(_ts("09:35"), _ts("09:40"))
    assert hi - lo == 5
    trig = minutes.first_trigger(lo, hi, long_level=101.0, short_level=99.0)
    assert trig.side == "buy" and trig.base_price == pytest.approx(101.4)
    assert minutes.index[trig.index] == _ts("09:37")
    assert minutes.first_trigger(lo, hi, long_level=None, short_level=99.0) is None

    coarse = SessionMinutes(frame[frame.index.minute % 5 == 0])
    assert not coarse.minute_resolution
    assert not SessionMinutes(frame.iloc[:1]).minute_resolution


def test_entry_minute_touching_own_stop_is_ambiguous():
    frame = _session({"09:37": (100.0, 101.2, 98.9, 100.0)})
    minutes = split_sessions(frame)[pd.Timestamp(datetime(*DAY)).date()]
    lo, hi = minutes.span(_ts("09:35"), _ts("09:40"))

    trig = minutes.first_trigger(lo, hi, long_level=101.0, short_level=None, long_stop=99.0)

    assert trig.ambiguous
