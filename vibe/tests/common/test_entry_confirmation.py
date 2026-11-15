from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.core.execution_realism import ExecutionRealismConfig
from vibe.backtester.runner import RuleSetRunner
from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.models import EntryConfirmation, RangeATRFilter, EMAConfirmation
from vibe.common.ruleset.support import UnsupportedRulesetSettingsError, require_supported
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig

VARIANT = Path("config/research/variants/orb_edge_2026/entry_stack_15m.yaml")


def _context(short=False):
    index = pd.date_range("2024-11-04 09:30", periods=4, freq="5min", tz="America/New_York")
    bars = pd.DataFrame({
        "timestamp": index, "open": [100., 100., 100., 100.],
        "high": [101., 101., 101., 103.], "low": [99., 99., 99., 99.8],
        "close": [100., 100., 100., 102.8], "volume": 2000.,
        "ATR_14": 1., "DAILY_ATR_20_WILDER": 5.,
        "RVOL_SAME_TIME_20": 2., "SESSION_VWAP_HLC3": 100.2,
        "EMA_9": 100.5, "EMA_20": 100.2, "EMA_SPREAD_CHANGE_9_20": 0.1,
    })
    if short:
        bars.loc[3, ["open", "high", "low", "close"]] = [100., 100.2, 97., 97.2]
        bars["SESSION_VWAP_HLC3"] = 99.8
        bars["EMA_9"] = 99.5
        bars["EMA_20"] = 99.8
        bars["EMA_SPREAD_CHANGE_9_20"] = -0.1
    return bars


def _strategy():
    return RuleSetRunner(RuleSetLoader.from_yaml(VARIANT)).strategy


@pytest.mark.parametrize("short", [False, True])
def test_complete_stack_accepts_long_and_short_with_existing_exit_policy(short):
    bars = _context(short)
    signal, meta = _strategy().generate_signal_incremental("TEST", bars.iloc[-1].to_dict(), bars)
    assert signal == (-1 if short else 1)
    assert meta["take_profit"] is None
    assert meta["stop_loss"] == (101. if short else 99.)
    assert meta["orb_range_atr_ratio"] == 0.4


@pytest.mark.parametrize("column,value,reason", [
    ("RVOL_SAME_TIME_20", 1.5, "breakout_relative_volume_below_threshold"),
    ("SESSION_VWAP_HLC3", 103., "vwap_not_aligned"),
    ("EMA_9", 100., "ema_not_aligned"),
    ("EMA_SPREAD_CHANGE_9_20", 0., "ema_not_expanding"),
    ("DAILY_ATR_20_WILDER", 10., "opening_range_outside_atr_bounds"),
    ("RVOL_SAME_TIME_20", np.nan, "entry_indicator_unavailable"),
])
def test_each_gate_rejects_with_explicit_reason(column, value, reason):
    bars = _context()
    bars[column] = value
    signal, meta = _strategy().generate_signal_incremental("TEST", bars.iloc[-1].to_dict(), bars)
    assert signal == 0
    assert meta["reason"] == reason


def test_close_mode_rejects_open_only_or_wick_only_crossing():
    bars = _context()
    bars.loc[3, ["open", "high", "low", "close"]] = [102., 103., 99., 100.]
    strategy = _strategy()
    assert strategy.generate_signal_incremental("TEST", bars.iloc[-1].to_dict(), bars)[0] == 0
    # Historical body mode still accepts an open beyond the range.
    strategy.config.breakout_evaluation = "body"
    strategy.config.entry_confirmation = None
    strategy.config.range_atr_filter = None
    assert strategy.generate_signal_incremental("TEST", bars.iloc[-1].to_dict(), bars)[0] == 1


def test_range_and_candle_boundaries_and_incomplete_window():
    bars = _context()
    strategy = _strategy()
    for ratio in (0.25, 0.60):
        bars["DAILY_ATR_20_WILDER"] = 2 / ratio
        assert strategy.generate_signal_incremental("TEST", bars.iloc[-1].to_dict(), bars)[0] == 1
    missing = bars.drop(index=1)
    assert _strategy().generate_signal_incremental("TEST", missing.iloc[-1].to_dict(), missing)[1][
        "reason"
    ] == "incomplete_opening_range"
    early = bars.iloc[:3]
    assert _strategy().generate_signal_incremental("TEST", early.iloc[-1].to_dict(), early)[1][
        "reason"
    ] == "opening_range_not_complete"


def test_future_context_is_refused_instead_of_using_future_indicator_row():
    bars = _context()
    with pytest.raises(ValueError, match="current decision bar"):
        _strategy().generate_signal_incremental("TEST", bars.iloc[2].to_dict(), bars)


def test_opt_in_filters_refuse_unsupported_execution_paths():
    ruleset = RuleSetLoader.from_yaml(VARIANT)
    require_supported(ruleset, "backtest")
    with pytest.raises(UnsupportedRulesetSettingsError, match="live"):
        require_supported(ruleset, "live")
    for policy in ("resting_stop", "at_stop_trigger"):
        with pytest.raises(UnsupportedRulesetSettingsError):
            BacktestEngine(ruleset, execution_realism=ExecutionRealismConfig.realistic(entry_fill_policy=policy))
    with pytest.raises(NotImplementedError):
        _strategy().generate_signals(_context())


def test_default_policy_has_no_new_gates_or_feature_work():
    strategy = ORBStrategy(ORBStrategyConfig(name="legacy"))
    bars = _context()
    assert not strategy.needs_entry_features
    assert strategy.prepare_entry_features(bars) is bars


def test_reusable_filter_config_rejects_invalid_or_misspelled_parameters():
    for create in (
        lambda: EntryConfirmation(),
        lambda: EntryConfirmation(vwap={"slope": True}),
        lambda: RangeATRFilter(min_ratio=0.8, max_ratio=0.6),
        lambda: EMAConfirmation(fast_period=20, slow_period=9),
    ):
        with pytest.raises(ValidationError):
            create()


def _history():
    frames = []
    for day in pd.bdate_range("2024-09-02", periods=25):
        stamps = pd.date_range(day.replace(hour=9, minute=30), periods=5, freq="5min").tz_localize(
            "America/New_York"
        )
        frames.append(pd.DataFrame({
            "open": 100., "high": [101., 101., 101., 103., 103.],
            "low": [99., 99., 99., 97., 97.], "close": 100., "volume": 1000.,
        }, index=stamps))
    bars = pd.concat(frames)
    bars.iloc[-2] = [100., 103., 99.8, 102.8, 6000.]
    bars.iloc[-1] = [103., 104., 102., 103.5, 1000.]
    return bars


def test_prepared_and_prefix_computed_features_produce_identical_decisions():
    history = _history()
    history["timestamp"] = history.index
    history["ATR_14"] = 1.
    full = _strategy().prepare_entry_features(history)
    for length in (3, len(history) - 2, len(history) - 1, len(history)):
        prefix = history.iloc[:length]
        prepared = _strategy().prepare_entry_features(prefix)
        columns = ["DAILY_ATR_20_WILDER", "RVOL_SAME_TIME_20", "SESSION_VWAP_HLC3",
                   "EMA_9", "EMA_20", "EMA_SPREAD_CHANGE_9_20"]
        pd.testing.assert_frame_equal(full.iloc[:length][columns], prepared[columns])
        current = prefix.iloc[-1].to_dict()
        assert _strategy().generate_signal_incremental("TEST", current, full.iloc[:length])[0] == (
            _strategy().generate_signal_incremental("TEST", current, prefix)[0]
        )
    assert _strategy().generate_signal_incremental(
        "TEST", history.iloc[-2].to_dict(), full.iloc[:-1]
    )[0] == 1


@pytest.mark.parametrize("entry_bar_stop", [False, True])
def test_engine_wires_entry_features_and_fills_only_after_completed_signal(
    tmp_path, monkeypatch, entry_bar_stop
):
    from vibe.backtester.core.portfolio import PortfolioManager

    snapshots = []
    update_equity = PortfolioManager.update_equity

    def observe(portfolio, bars, stamp):
        update_equity(portfolio, bars, stamp)
        snapshots.append((stamp, portfolio.equity_curve[-1][1], bool(portfolio.positions)))

    monkeypatch.setattr(PortfolioManager, "update_equity", observe)
    history = _history()
    if entry_bar_stop:
        history.iloc[-1] = [103., 104., 98., 100., 1000.]
    history.to_parquet(tmp_path / "TEST.parquet")
    engine = BacktestEngine(
        RuleSetLoader.from_yaml(VARIANT),
        data_dir=tmp_path,
        execution_realism=ExecutionRealismConfig.realistic(entry_fill_policy="at_next_bar_open"),
    )
    last_day = history.index[-1].date()
    result = engine.run("TEST", history.index[0].to_pydatetime(), history.index[-1].to_pydatetime(),
                        graded_start=last_day)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time == history.index[-1].to_pydatetime()
    assert trade.entry_price == pytest.approx(103.02)
    assert result.execution_diagnostics["entry_filter_evaluations_passed"] == 1
    assert result.execution_diagnostics["accounting_failures"] == 0
    assert not any(f.applicable and not f.passed for f in result.engine_integrity_report.findings)
    if entry_bar_stop:
        assert trade.exit_time == trade.entry_time
        assert trade.exit_price < 99.
    before_entry = [snapshot for snapshot in snapshots if snapshot[0] < trade.entry_time]
    assert before_entry
    assert all(equity == engine.initial_capital and not exposed
               for _, equity, exposed in before_entry)


def test_completed_opening_range_uses_actual_timeframe_and_refuses_partial_bar_window():
    from vibe.backtester.runner import RuleSetRunner

    ruleset = RuleSetLoader.from_yaml(VARIANT)
    ruleset.instruments.timeframe = "1m"
    assert RuleSetRunner(ruleset).strategy.config.bar_minutes == 1
    ruleset.instruments.timeframe = "7m"
    with pytest.raises(UnsupportedRulesetSettingsError, match="whole number"):
        RuleSetRunner(ruleset)
    with pytest.raises(ValueError, match="whole opening-range bars"):
        ORBStrategy(ORBStrategyConfig(name="TEST", breakout_evaluation="close",
                                      orb_duration_minutes=7, bar_minutes=5))


def test_completed_bar_filters_refuse_custom_execution_with_unverified_timing():
    from vibe.backtester.core.execution.config import ExecutionConfig

    with pytest.raises(UnsupportedRulesetSettingsError, match="custom execution_config"):
        BacktestEngine(
            RuleSetLoader.from_yaml(VARIANT), execution_config=ExecutionConfig.legacy(),
            execution_realism=ExecutionRealismConfig.realistic(entry_fill_policy="at_next_bar_open"),
        )
