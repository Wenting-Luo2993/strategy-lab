"""First-candle-direction ORB (Zarattini & Aziz 2023) on synthetic sessions.

Every session sits at 100 with an opening candle (09:30-09:34) that opens at
100, reaches 101 and 99, and closes wherever the test puts it. Entries are
priced AT_NEXT_BAR_OPEN: the 09:35 open (100) plus the engine's 2-tick entry
slippage. The target is 10R from that fill.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import (
    EntryFillPolicy,
    ExecutionRealismConfig,
)
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.support import (
    UnsupportedRulesetSettingsError,
    require_supported,
    unsupported_settings,
)

ET = ZoneInfo("America/New_York")
SYMBOL = "TEST"
DAY = (2024, 1, 2)
SLIP = 0.02
_RULESET = (
    Path(__file__).resolve().parents[3] / "vibe" / "rulesets" / "orb_first_candle_za.yaml"
)


def _ts(hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime(*DAY, h, m, tzinfo=ET))


def _session(first_close: float, overrides=None) -> pd.DataFrame:
    idx = pd.date_range(_ts("09:30"), _ts("15:59"), freq="1min")
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 10_000.0},
        index=idx,
    )
    df.loc[_ts("09:31"), "high"] = 101.0
    df.loc[_ts("09:33"), "low"] = 99.0
    df.loc[_ts("09:34"), "close"] = first_close
    for hhmm, (o, h, l, c) in (overrides or {}).items():
        df.loc[_ts(hhmm), ["open", "high", "low", "close"]] = [o, h, l, c]
    df.index.name = "ts_event"
    return df


def _ruleset(**strategy):
    ruleset = RuleSetLoader.from_yaml(_RULESET)
    data = ruleset.model_dump()
    data["instruments"]["symbols"] = [SYMBOL]
    data["strategy"].update(strategy)
    return type(ruleset).model_validate(data)


def _engine(tmp_path, *, ruleset=None, policy=EntryFillPolicy.AT_NEXT_BAR_OPEN):
    return BacktestEngine(
        ruleset or _ruleset(),
        data_dir=tmp_path,
        execution_realism=ExecutionRealismConfig.realistic(entry_fill_policy=policy),
    )


def _run(tmp_path, frame, **kwargs):
    frame.to_parquet(tmp_path / f"{SYMBOL}.parquet")
    return _engine(tmp_path, **kwargs).run(
        SYMBOL,
        datetime(*DAY, tzinfo=ET),
        datetime(*DAY, 23, 59, tzinfo=ET),
    )


def _risk_per_share(trade) -> float:
    return trade.initial_risk / trade.quantity


def test_green_first_candle_goes_long_at_next_open_and_exits_eod(tmp_path):
    (trade,) = _run(tmp_path, _session(100.5)).trades

    assert trade.side == "buy"
    assert trade.entry_price == pytest.approx(100.0 + SLIP)
    # Stop is the first candle's low.
    assert _risk_per_share(trade) == pytest.approx(100.0 + SLIP - 99.0)
    assert trade.exit_reason == "EOD"
    assert trade.exit_time == _ts("15:55")


def test_red_first_candle_goes_short_with_stop_at_candle_high(tmp_path):
    (trade,) = _run(tmp_path, _session(99.5)).trades

    assert trade.side == "sell"
    assert trade.entry_price == pytest.approx(100.0 - SLIP)
    assert _risk_per_share(trade) == pytest.approx(101.0 - (100.0 - SLIP))
    assert trade.exit_reason == "EOD"


def test_doji_first_candle_does_not_trade(tmp_path):
    assert _run(tmp_path, _session(100.0)).trades == []


def test_long_stops_at_first_candle_low(tmp_path):
    frame = _session(100.5, {"10:00": (100.0, 100.1, 98.5, 98.6)})

    (trade,) = _run(tmp_path, frame).trades

    assert trade.exit_reason == "STOP"
    assert trade.exit_price <= 99.0
    assert trade.exit_price >= 99.0 - 0.05


def test_ten_r_target_is_anchored_at_the_fill(tmp_path):
    fill = 100.0 + SLIP
    target = fill + 10 * (fill - 99.0)
    frame = _session(100.5, {"11:00": (100.0, target + 0.5, 99.9, 100.0)})

    (trade,) = _run(tmp_path, frame).trades

    assert trade.exit_reason == "TP"
    assert trade.exit_price == pytest.approx(target)
    gross_r = (trade.exit_price - trade.entry_price) / _risk_per_share(trade)
    assert gross_r == pytest.approx(10.0)


def test_target_needs_trade_through(tmp_path):
    fill = 100.0 + SLIP
    target = fill + 10 * (fill - 99.0)
    # Touches the target exactly: the realistic profile needs one tick through.
    frame = _session(100.5, {"11:00": (100.0, target, 99.9, 100.0)})

    (trade,) = _run(tmp_path, frame).trades

    assert trade.exit_reason == "EOD"


def test_r_multiple_maps_to_strategy_and_reports_no_price():
    from vibe.common.ruleset.models import RMultipleTakeProfit
    from vibe.common.strategies.orb import take_profit_settings

    assert take_profit_settings(RMultipleTakeProfit(multiplier=10)) == {
        "take_profit_method": "r_multiple",
        "take_profit_multiplier": 10.0,
    }
    with pytest.raises(ValueError):
        RMultipleTakeProfit(multiplier=0)


@pytest.mark.parametrize(
    "policy", [EntryFillPolicy.RESTING_STOP, EntryFillPolicy.AT_STOP_TRIGGER]
)
def test_breakout_level_entry_policies_are_rejected(tmp_path, policy):
    with pytest.raises(UnsupportedRulesetSettingsError, match="first_candle_direction"):
        _engine(tmp_path, policy=policy)


def test_backtest_supported_live_rejected():
    ruleset = _ruleset()
    require_supported(ruleset, "backtest")
    live = unsupported_settings(ruleset, "live")
    assert any("entry_mode" in p for p in live)
    assert any("r_multiple" in p for p in live)


@pytest.mark.parametrize(
    "updates, fragment",
    [
        ({"orb_body_pct_filter": 0.5}, "orb_body_pct_filter"),
        ({"breakout_evaluation": "body"}, "breakout_evaluation"),
    ],
)
def test_breakout_bar_filters_are_rejected(updates, fragment):
    problems = unsupported_settings(_ruleset(**updates), "backtest")
    assert any(fragment in p for p in problems), problems


def test_unknown_entry_mode_still_rejected():
    problems = unsupported_settings(_ruleset(entry_mode="aggressive"), "backtest")
    assert any("entry_mode" in p for p in problems)
